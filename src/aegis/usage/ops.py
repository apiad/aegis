"""The usage operations: usage.read for agents and the CLI's questions (MCP
``usage_read``), and usage.page for the browser's usage page. Queries run in
a thread on a read-only connection, as the journal's searches do; the writer
thread owns writes.

A member sees only the parts of sessions it owns (``App.sees`` over
``usage_turns.person``, the session's owner); an admin sees all. A call that
arrives over a link answers for this server only; a person's call asks every
linked server too, and merges their answers (``far``, ``across``)."""

from __future__ import annotations

import asyncio
import copy
import json
import pwd
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..journal import db, when
from ..ops import OpError
from . import page, tables
from .context import threshold
from .importer import ImportReport, aegis_known, import_claude_projects
from .query import BYS, DIMS, MEASURES, UsageQuery, merge_subs, run

if TYPE_CHECKING:
    from ..app import App
    from ..ops import Principal

WEEKS = re.compile(r"(\d+)w")
EPOCH = re.compile(r"\d+(\.\d+)?")


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ReadParams(_Strict):
    since: str = Field(
        "4w",
        description="First day: 2026-10-09, today, yesterday, 7d, 4w (the last 4"
        " weeks) or epoch seconds.",
    )
    until: str | None = Field(
        None, description="Last day, included, in the same forms; now by default."
    )
    by: str | None = Field(
        None, description="A period per row: " + ", ".join(BYS) + "."
    )
    split: list[str] = Field(
        default_factory=list,
        description="Dimensions to group by: " + ", ".join(DIMS) + ".",
    )
    measure: str = Field(
        "cost",
        description="What rows are sorted by: "
        + ", ".join(MEASURES)
        + ". Every row carries all of them.",
    )
    where: dict[str, str] = Field(
        default_factory=dict,
        description='Only parts whose dimension has this value: {"mode": "queue"}.',
    )
    server: str | None = Field(
        None,
        description="The server to answer for: this one, or all to merge every"
        " linked server's answer. All by default for a person, this one for an agent.",
    )
    whatif: str | None = Field(
        None,
        description="A context limit to replay handoffs at, 100k, 150k, 200k, 300k"
        " or 400k: fills the whatif measure.",
    )
    tz: str | None = Field(
        None, description="IANA time zone for periods; the server's by default."
    )


def _bound(text: str | None, end: bool, now: float) -> float | None:
    if text and (m := WEEKS.fullmatch(text.strip().lower())):
        return now - int(m[1]) * 7 * 86400
    if text and EPOCH.fullmatch(text.strip()):
        return float(text)
    return when.bound(text, end=end, now=now)


def query_of(p: ReadParams, now: float) -> UsageQuery:
    """``p`` as a query, or an OpError naming what it allows."""
    from .cli import _local_zone

    if p.by is not None and p.by not in BYS:
        raise OpError("bad_params", f"by takes {', '.join(BYS)}, not {p.by!r}")
    if p.measure not in MEASURES:
        raise OpError(
            "bad_params", f"measure takes {', '.join(MEASURES)}, not {p.measure!r}"
        )
    for d in (*p.split, *p.where):
        if d not in DIMS:
            raise OpError(
                "bad_params", f"split and where take {', '.join(DIMS)}, not {d!r}"
            )
    try:
        since = _bound(p.since, False, now) or 0.0
        until = _bound(p.until, True, now) or now + 1
        whatif = threshold(p.whatif) if p.whatif is not None else None
    except ValueError as e:
        raise OpError("bad_params", str(e)) from None
    return UsageQuery(
        since,
        until,
        by=p.by,
        split=tuple(p.split),
        measure=p.measure,
        where=dict(p.where),
        tz=p.tz or _local_zone(),
        whatif=whatif,
    )


def people(app: "App", con, who: "Principal") -> set[str] | None:
    """The owners whose sessions ``who`` may see, as usage_turns names them;
    None for all."""
    if app.multi_user is None or who.is_admin:
        return None
    names = [n for (n,) in con.execute("SELECT DISTINCT person FROM usage_turns")]
    return {n for n in names if app.sees(who, n)}


def answer(app: "App", q: UsageQuery, who: "Principal") -> dict:
    """``q`` over this server's tables, as usage.read returns it."""
    con = db.open_read(app.journal.path)
    try:
        q.people = people(app, con, who)
        try:
            usage = app.config.current().usage
            a = run(
                con,
                q,
                owners=usage.get("owners"),
                subscriptions=usage.get("subscriptions"),
            )
        except ValueError as e:
            raise OpError("bad_params", str(e)) from None
        if "session" in q.split:
            handles = dict(
                con.execute(
                    "SELECT log_id, handle FROM handles ORDER BY rowid"
                ).fetchall()
            )
            live = getattr(app, "sessions", None)
            for r in a.rows:
                r["handle"] = handles.get(r["session"], "")
                r["open"] = live is not None and r["session"] in live.sessions
    finally:
        con.close()
    name = app.server_name
    for r in a.rows:
        if "server" in r:
            r["server"] = name
    key = q.measure
    a.rows.sort(key=lambda r: (str(r.get("bucket", "")), -r[key]))
    return {
        "server": name,
        "at": time.time(),
        **asdict(a),
        "backfill": app.journal.usage_progress() or None,
    }


def params_of(q: UsageQuery) -> dict:
    """``q`` as usage.read's params, for a linked server."""
    return {"since": repr(q.since), "until": repr(q.until), "by": q.by,
            "split": list(q.split), "measure": q.measure, "where": dict(q.where),
            "tz": q.tz}  # fmt: skip


async def far(app: "App", caller, who: "Principal", asks: dict, last: dict) -> dict:
    """Ask every linked server each question in ``asks`` (a key to usage.read
    params): per server, its state, when it answered and its answers by key.
    A server that fails serves the last answers ``last`` kept, as cached; one
    never answered (down, or an aegis with no usage.read) is missing. A call
    over a link and a member's call ask no link: links are the admin's."""
    links = getattr(app, "links", None)
    member = app.multi_user is not None and not who.is_admin
    if links is None or caller.link is not None or member:
        return {}
    up = links.up()

    async def one(link):
        got = await asyncio.gather(
            *(link.call("usage.read", {**p, "server": link.name}) for p in asks.values()),
            return_exceptions=True,
        )  # fmt: skip
        for g in got:
            if isinstance(g, OpError):
                return None
            if isinstance(g, BaseException):
                raise g
        return dict(zip(asks, got))

    fresh = dict(zip([link.name for link in up], await asyncio.gather(*map(one, up))))
    now = time.time()
    out = {}
    names = [*fresh, *(link.name for link in links.links()), *(n for n, _ in last)]
    for name in dict.fromkeys(names):
        if (got := fresh.get(name)) is not None:
            last.update({(name, k): a for k, a in got.items()})
            state = "live"
        else:
            got = {k: last[name, k] for k in asks if (name, k) in last}
            state = "cached" if got and len(got) == len(asks) else "missing"
            got = got if state == "cached" else {}
        at = min((a["at"] for a in got.values()), default=None)
        out[name] = {"state": state, "at": at,
                     "age_s": None if at is None else max(round(now - at), 0),
                     "answers": copy.deepcopy(got)}  # fmt: skip
    return out


def states(here: str, at: float, servers: dict) -> dict:
    """Each server's state, this one first."""
    return {here: {"at": at, "age_s": 0, "state": "live"}} | {
        n: {k: s[k] for k in ("at", "age_s", "state")} for n, s in servers.items()
    }


def _merge(app: "App", qs: dict, answers: dict[str, dict[str, dict]]) -> None:
    """Split subscriptions once over every server's hours, per question."""
    if len(answers) < 2:
        return
    subs = app.config.current().usage.get("subscriptions") or {}
    con = db.open_read(app.journal.path)
    try:
        for key, q in qs.items():
            merge_subs(con, q, {s: a[key] for s, a in answers.items()}, subs)
    finally:
        con.close()


def across(app: "App", q: UsageQuery, who: "Principal", servers: dict) -> dict:
    """``q`` here and on every linked server that answered, merged: rows carry
    their server, totals add up, a subscription counts once."""
    here = answer(app, q, who)
    answers = {app.server_name: {"q": here}} | {
        n: {"q": next(iter(s["answers"].values()))}
        for n, s in servers.items()
        if s["answers"]
    }
    _merge(app, {"q": q}, answers)
    each = {n: a["q"] for n, a in answers.items()}
    rows = [{**r, "server": n} for n, a in each.items() for r in a["rows"]]
    rows.sort(key=lambda r: (str(r.get("bucket", "")), -r[q.measure]))
    totals = {m: sum(a["totals"].get(m, 0) for a in each.values())
              for m in here["totals"]}  # fmt: skip
    totals["cost"] = round(totals["cost"], 9)
    cost = totals["cost"]
    priced = sum(a["estimated"] * a["totals"]["cost"] for a in each.values())
    return here | {
        "rows": rows,
        "totals": totals,
        "outcomes": [{**o, "server": n} for n, a in each.items() for o in a["outcomes"]],
        "estimated": round(priced / cost, 6) if cost else 0.0,
        "unpriced_tokens": sum(a["unpriced_tokens"] for a in each.values()),
        "sub_hours": {},
        "servers": states(app.server_name, here["at"], servers),
    }  # fmt: skip


class PageParams(_Strict):
    window: int = Field(4, ge=1, le=52, description="Weeks, ending this one.")
    split: str = "purpose"
    measure: str = "cost"
    servers: list[str] | None = None
    tz: str | None = None


# The page's controls: what each offers.
WINDOWS = (1, 4, 12, 26)
PAGE_SPLITS = ("purpose", "repo", "owner", "mode", "model", "harness", "activity",
               "person", "server")  # fmt: skip


def page_of(
    app: "App",
    p: PageParams,
    who: "Principal",
    now: float,
    qs: dict[str, UsageQuery],
    servers: dict | None = None,
) -> dict:
    """Every query the page needs, here and from the linked ``servers``
    (from ``far``, keyed by panel), and its panels over the servers shown."""
    servers = servers or {}
    here = app.server_name
    answers = {here: {name: answer(app, q, who) for name, q in qs.items()}}
    answers |= {n: s["answers"] for n, s in servers.items() if s["answers"]}
    _merge(app, qs, answers)
    if p.servers is not None:
        answers = {n: a for n, a in answers.items() if n in p.servers} or {
            here: answers[here]
        }
    *_, mondays = page.window(p.window, now, qs["total"].tz)
    conf = app.config.current()
    names = conf.usage.get("owner_names") or {}
    out = page.panels(
        answers, p.window, p.split, p.measure, names, mondays,
        handoff_at=conf.context["handoff_at"],
    )  # fmt: skip
    return out | {
        "at": now,
        "since": mondays[0],
        "window": p.window,
        "split": p.split,
        "measure": p.measure,
        "servers": states(here, now, servers),
        "controls": {
            "windows": list(WINDOWS),
            "splits": list(PAGE_SPLITS),
            "measures": [{"key": k, "label": v} for k, v in page.TITLES.items()],
            "servers": [
                {"name": n, "on": n in answers} for n in (here, *servers)
            ],  # fmt: skip
        },
    }


class ImportParams(_Strict):
    people: list[str] | None = Field(
        None, description="whose files to read, by token name; default everyone"
    )


def import_sources(app: "App", people: list[str] | None) -> list[tuple]:
    """(person, Linux user, home) for each person whose Claude Code files
    the import reads: the server's own user's, and each member's read as its
    Linux user. A token without a user of its own acts as the server."""
    out = []
    for row in app.principals.rows():
        name, user = str(row["name"]), row["linux_user"]
        if people is not None and name not in people:
            continue
        if row["owner"]:
            out.append((name, None, Path.home()))
        elif user:
            try:
                out.append((name, user, Path(pwd.getpwnam(user).pw_dir)))
            except KeyError:
                continue  # aegis warns at boot
    return out


def register_usage_ops(app: "App") -> None:
    r = app.registry
    # The last answer from each linked server, by (server, question): what a
    # server that stops answering is shown with, marked cached.
    last: dict[tuple[str, str], dict] = {}

    @r.op("usage.read", ReadParams, agent=True, read=True)
    async def usage_read(p: ReadParams, caller):
        """What work cost on this server, from the usage tables aegis fills as
        each turn ends: cost at API prices, tokens, agent seconds, turns,
        sessions, tool calls and waits, over a window, grouped by any of the
        split's dimensions and by period. Returns rows, totals and the
        journal's outcomes (commits, PRs, closed issues) for the same slice.
        sub and limit are a subscription's fee and weekly limit shared by
        cost; whatif (with whatif=200k) what handing off there would save,
        net of rewriting and rereading.
        Background work is mode queue, workflow, schedule, monitor or agent."""
        if caller.link is None and p.server not in (None, "all", app.server_name):
            raise OpError(
                "bad_params",
                f"usage_read answers for this server ({app.server_name}) or all",
            )
        q = query_of(p, time.time())
        who = app.who(caller)
        alone = p.server == app.server_name or (p.server is None and caller.is_agent)
        if caller.link is not None or alone:
            return await asyncio.to_thread(answer, app, q, who)
        params = p.model_dump(exclude={"server"})
        key = json.dumps(params, sort_keys=True)
        servers = await far(app, caller, who, {key: params}, last)
        return await asyncio.to_thread(across, app, q, who, servers)

    @r.op("usage.import", ImportParams, agent=True, admin=True)
    async def usage_import(p: ImportParams, caller):
        """Count once the sessions Claude Code ran outside aegis: each
        person's ~/.claude/projects, read as their Linux user, as mode
        outside. Messages aegis already counts are skipped, and a file
        already imported is not read again. Claude Code purges its files
        after 30 days, so run it soon after upgrading, on each server."""
        sources = import_sources(app, p.people)
        known = await asyncio.to_thread(aegis_known, app.roots.state_root)

        def job(con) -> dict:
            tables.ensure(con)
            total = ImportReport()
            for person, user, home in sources:
                r = import_claude_projects(
                    con, [home / ".claude" / "projects"], known, person, user=user
                )
                for k in ("files", "sessions", "turns", "already", "live"):
                    setattr(total, k, getattr(total, k) + getattr(r, k))
                total.unreadable += r.unreadable
            return asdict(total)

        return await asyncio.wrap_future(app.journal.run(job))

    @r.op("usage.page", PageParams)
    async def usage_page(p: PageParams, caller):
        """The usage page's panels: tiles, trend, owners, repo by purpose,
        when, driven by, harness and model, outcomes and the footer."""
        if p.split not in PAGE_SPLITS:
            raise OpError(
                "bad_params", f"split takes {', '.join(PAGE_SPLITS)}, not {p.split!r}"
            )
        if p.measure not in page.MEASURES:
            raise OpError(
                "bad_params",
                f"measure takes {', '.join(page.MEASURES)}, not {p.measure!r}",
            )
        from .cli import _local_zone

        who, now = app.who(caller), time.time()
        qs = page.queries(p.window, p.split, now, p.tz or _local_zone())
        tag = f"page:{p.window}:{p.split}:"
        asks = {tag + name: params_of(q) for name, q in qs.items()}
        servers = await far(app, caller, who, asks, last)
        for s in servers.values():
            s["answers"] = {k.removeprefix(tag): a for k, a in s["answers"].items()}
        return await asyncio.to_thread(page_of, app, p, who, now, qs, servers)
