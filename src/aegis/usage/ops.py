"""The usage operations: usage.read for agents and the CLI's questions (MCP
``usage_read``), and usage.page for the browser's usage page. Queries run in
a thread on a read-only connection, as the journal's searches do; the writer
thread owns writes.

A member sees only the parts of sessions it owns (``App.sees`` over
``usage_turns.person``, the session's owner); an admin sees all. A call that
arrives over a link answers for this server only."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..journal import db, when
from ..ops import OpError
from .query import BYS, DIMS, MEASURES, UsageQuery, run

if TYPE_CHECKING:
    from ..app import App
    from ..ops import Principal

WEEKS = re.compile(r"(\d+)w")


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ReadParams(_Strict):
    since: str = Field(
        "4w",
        description="First day: 2026-10-09, today, yesterday, 7d or 4w (the last 4 weeks).",
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
        None, description="The server to answer for; this one by default."
    )
    whatif: str | None = Field(
        None, description="A context limit to replay handoffs at, such as 200k."
    )
    tz: str | None = Field(
        None, description="IANA time zone for periods; the server's by default."
    )


def _bound(text: str | None, end: bool, now: float) -> float | None:
    if text and (m := WEEKS.fullmatch(text.strip().lower())):
        return now - int(m[1]) * 7 * 86400
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
            a = run(con, q, owners=app.config.current().usage.get("owners"))
        except ValueError as e:
            raise OpError("bad_params", str(e)) from None
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


def register_usage_ops(app: "App") -> None:
    r = app.registry

    @r.op("usage.read", ReadParams, agent=True, read=True)
    async def usage_read(p: ReadParams, caller):
        """What work cost on this server, from the usage tables aegis fills as
        each turn ends: cost at API prices, tokens, agent seconds, turns,
        sessions, tool calls and waits, over a window, grouped by any of the
        split's dimensions and by period. Returns rows, totals and the
        journal's outcomes (commits, PRs, closed issues) for the same slice.
        Background work is mode queue, workflow, schedule, monitor or agent."""
        if p.whatif is not None:
            raise OpError("bad_params", "whatif is not counted yet")
        if caller.link is None and p.server not in (None, app.server_name):
            raise OpError(
                "bad_params",
                f"usage_read answers for this server ({app.server_name}) only",
            )
        q = query_of(p, time.time())
        return await asyncio.to_thread(answer, app, q, app.who(caller))
