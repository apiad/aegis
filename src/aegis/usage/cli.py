"""``aegis usage``: what the agents' work cost, read from aegis's own store.

The bare command reads the usage tables the journal's writer thread fills
(``query.py``, ``render.py``); ``repo`` and ``repos`` price
a git repository's agent work against every transcript store (``measure.py``).
Every path comes from the roots the top-level CLI builds (``cli.roots_here``),
the one place that reads the working directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, no_args_is_help=False)


def _state(state: str | None, root: Path | None) -> Path:
    from ..cli import roots_here

    return Path(state) if state else roots_here(root).state_root


def _cost_options(
    since: str | None,
    until: str | None,
    no_foreign: bool,
    exclude: list[str] | None,
    split: str | None,
    extra_root: list[str] | None,
    state: Path,
):
    from .locality import SPLIT_DIRS
    from .measure import CostOptions

    named_splits = frozenset(s for s in (split or "").split(",") if s)
    return CostOptions(
        since=since,
        until=until,
        split_dirs=named_splits or SPLIT_DIRS,
        exclude=tuple(exclude or ()),
        foreign=not no_foreign,
        extra_roots=tuple(Path(p) for p in (extra_root or ())),
        state_dir=state,
    )


SINCE = typer.Option(None, "--since", help="ISO date lower bound")
UNTIL = typer.Option(None, "--until", help="ISO date upper bound")
NO_FOREIGN = typer.Option(False, "--no-foreign", help="skip ~/.claude/projects")
EXCLUDE = typer.Option(
    None, "--exclude", help="glob dropped from the git side (repeatable)"
)
SPLIT = typer.Option(None, "--split", help="comma-separated monorepo containers")
EXTRA_ROOT = typer.Option(
    None,
    "--extra-root",
    help="another directory of claude transcripts, .jsonl or .jsonl.gz (repeatable)",
)
STATE = typer.Option(None, "--state", help="override the aegis state dir")
ROOT = typer.Option(
    None,
    "--root",
    help="Config root; default: the nearest ancestor holding .aegis.yaml.",
)
AS_JSON = typer.Option(False, "--json", help="print the full structure")


@app.command("repo")
def repo_cost(
    path: str = typer.Argument(..., help="path to the git repo to measure"),
    since: str = SINCE,
    until: str = UNTIL,
    no_foreign: bool = NO_FOREIGN,
    exclude: list[str] = EXCLUDE,
    split: str = SPLIT,
    extra_root: list[str] = EXTRA_ROOT,
    state: str = STATE,
    root: Path | None = ROOT,
    as_json: bool = AS_JSON,
) -> None:
    """What one repository cost to build, measured against its transcripts."""
    from .cost_render import repo_lines
    from .measure import measure

    target = Path(path)
    if not (target / ".git").exists():
        typer.echo(f"not a git repo: {target}")
        raise typer.Exit(2)
    options = _cost_options(
        since, until, no_foreign, exclude, split, extra_root, _state(state, root)
    )
    result = measure(target, options)
    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=1, default=str))
    else:
        typer.echo("\n".join(repo_lines(result)))


@app.command("repos")
def repos_cost(
    directory: str = typer.Argument(..., help="directory of git repos to sweep"),
    since: str = SINCE,
    until: str = UNTIL,
    no_foreign: bool = NO_FOREIGN,
    exclude: list[str] = EXCLUDE,
    split: str = SPLIT,
    extra_root: list[str] = EXTRA_ROOT,
    state: str = STATE,
    root: Path | None = ROOT,
    as_json: bool = AS_JSON,
) -> None:
    """Every git repo directly under a directory, measured in one sweep."""
    from .cost_render import sweep_lines
    from .measure import sweep

    target = Path(directory)
    if not target.is_dir():
        typer.echo(f"not a directory: {target}")
        raise typer.Exit(2)
    options = _cost_options(
        since, until, no_foreign, exclude, split, extra_root, _state(state, root)
    )
    rows = sweep(target, options)
    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in rows], indent=1, default=str))
    else:
        typer.echo("\n".join(sweep_lines(rows)))


BYS = ("hour", "day", "week", "month", "dow", "dow_hour")
RELATIVE = re.compile(r"^(\d+)([hdwm])$")
UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400, "m": 30 * 86400}


def _when(text: str | None, now: float, zone) -> float | None:
    """An ISO date or time, or a span back from now: 6h, 30d, 4w, 3m."""
    if not text:
        return None
    if m := RELATIVE.match(text):
        return now - int(m[1]) * UNITS[m[2]]
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        raise typer.BadParameter(f"{text!r} is not a date nor a span like 4w") from None
    return (d if d.tzinfo else d.replace(tzinfo=zone)).timestamp()


def _handles(con) -> dict[str, str]:
    """A handle per session, from the journal's handles table."""
    out: dict[str, str] = {}
    for handle, log_id in con.execute("SELECT handle, log_id FROM handles"):
        out.setdefault(log_id, handle)
    return out


def _progress(con, state: Path) -> str | None:
    """How far the usage backfill has come, when it has not finished."""
    from . import tables

    if tables.is_complete(con):
        return None
    done = con.execute("SELECT COUNT(DISTINCT log_id) FROM usage_turns").fetchone()[0]
    total = sum(1 for _ in (state / "transcripts").glob("*.jsonl"))
    return (
        f"usage backfill: {done} of about {total} sessions derived so far;"
        " this answer is partial"
    )


@app.callback(invoke_without_command=True)
def usage(
    ctx: typer.Context,
    by: str = typer.Option(None, "--by", help="hour, day, week, month, dow, dow_hour"),
    split: str = typer.Option(
        None,
        "--split",
        help="comma-separated dimensions: repo,purpose, owner, model...",
    ),
    measure: str = typer.Option(
        "cost", "--measure", help="cost, tokens, secs, turns, subscription, limit..."
    ),
    whatif: str = typer.Option(
        None, "--whatif", help="replay handoffs at 100k, 150k, 200k, 300k or 400k"
    ),
    where: list[str] = typer.Option(
        None, "--where", help="dimension=value, repeatable: harness=claude-code"
    ),
    sessions: bool = typer.Option(False, "--sessions", help="the costliest sessions"),
    tools: bool = typer.Option(False, "--tools", help="cost and calls by activity"),
    since: str = typer.Option(None, "--since", help="ISO date, or a span: 4w, 30d"),
    until: str = typer.Option(None, "--until", help="ISO date, or a span: 1w"),
    session: str = typer.Option(None, "--session", help="one session, by handle"),
    model: str = typer.Option(None, "--model", help="filter to one model"),
    tz: str = typer.Option(None, "--tz", help="IANA tz (default: system)"),
    as_json: bool = AS_JSON,
    state: str = STATE,
    root: Path | None = ROOT,
) -> None:
    """Usage and cost from the journal's usage tables, which aegis serve fills
    as each turn ends."""
    # The callback fires for a subcommand too; without this guard
    # `aegis usage repo` would print the dashboard first.
    if ctx.invoked_subcommand:
        return
    import sqlite3
    import time
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    from ..journal import db
    from .context import threshold
    from .query import DIMS, MEASURES, UsageQuery, run
    from .render import dashboard_lines, table_lines, value

    measure = {"subscription": "sub"}.get(measure, measure)
    at = None
    if whatif:
        try:
            at = threshold(whatif)
        except ValueError as e:
            typer.echo(f"--{e}")
            raise typer.Exit(2) from None
        if measure == "cost":
            measure = "whatif"
    if by and by not in BYS:
        typer.echo(f"--by takes {', '.join(BYS)}, not {by!r}")
        raise typer.Exit(2)
    if measure not in MEASURES:
        typer.echo(f"--measure takes {', '.join(MEASURES)}, not {measure!r}")
        raise typer.Exit(2)
    dims = tuple(d for d in (split or "").split(",") if d)
    filters: dict[str, str] = {}
    for w in where or []:
        k, eq, v = w.partition("=")
        if not eq or k not in DIMS:
            typer.echo(
                f"--where takes dimension=value over {', '.join(DIMS)}, not {w!r}"
            )
            raise typer.Exit(2)
        filters[k] = v
    for d in dims:
        if d not in DIMS:
            typer.echo(f"--split takes {', '.join(DIMS)}, not {d!r}")
            raise typer.Exit(2)
    if model:
        filters["model"] = model
    zone_name = tz or _local_zone()
    zone = ZoneInfo(zone_name)
    now = time.time()
    lo = _when(since, now, zone) or 0.0
    hi = _when(until, now, zone) or now + 1
    path = _state(state, root)
    try:
        con = db.open_read(path / "journal.db")
        if session:
            ids = db.log_ids(con, session)
            hit = con.execute(
                f"SELECT log_id FROM usage_turns WHERE log_id IN ({','.join('?' * len(ids))})"
                " ORDER BY ts DESC LIMIT 1",
                ids,
            ).fetchone()
            filters["session"] = hit[0] if hit else session
        note = _progress(con, path)
    except sqlite3.OperationalError:
        typer.echo("No sessions found: aegis serve has not counted any usage here yet.")
        raise typer.Exit(0) from None
    q = UsageQuery(lo, hi, where=filters, tz=zone_name, whatif=at)
    subs = _subscriptions(root)
    window = (
        f"{datetime.fromtimestamp(lo, zone):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(min(hi, now), zone):%Y-%m-%d} ({zone_name})"
        if lo
        else f"all → {datetime.fromtimestamp(min(hi, now), zone):%Y-%m-%d} ({zone_name})"
    )
    if note and not as_json:
        typer.echo(note)
    hour_of_day = by == "hour" and not as_json
    if sessions:
        dims, measure = ("session",), "cost"
    elif tools:
        dims = ("activity",)
    if dims or by:
        a = run(
            con,
            replace(q, split=dims, by="dow_hour" if hour_of_day else by),
            subscriptions=subs,
        )
        if hour_of_day:  # the hour of the day, as the old --by hour showed it
            hours: dict[str, dict] = {}
            for r in a.rows:
                h = r["bucket"][-2:]
                key = (*(r[d] for d in dims), h)
                acc = hours.setdefault(key, {**{d: r[d] for d in dims}, "bucket": h,
                                             **dict.fromkeys(MEASURES, 0)})  # fmt: skip
                for m in MEASURES:
                    acc[m] += r[m]
            a.rows = list(hours.values())
    else:
        a = run(con, q, subscriptions=subs)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "rows": a.rows,
                    "totals": a.totals,
                    "outcomes": a.outcomes,
                    "estimated": a.estimated,
                    "unpriced_tokens": a.unpriced_tokens,
                    "subs": a.subs,
                    "context": a.context,
                    "backfill": note,
                },
                indent=1,
            )
        )
        return
    if not a.totals["turns"]:
        typer.echo("No sessions found.")
        raise typer.Exit(0)
    names = _handles(con)
    if dims or by:
        lines = table_lines(a, dims, by, measure, names)
        r = next((r for r in (a.context or {}).get("replay", []) if r["at"] == at), None)
        if r is not None:
            net = r["saved"] - r["rewrite"] - r["handoffs"] * r["reread"]
            lines.append(
                f"Handing off at {at // 1000}k: {r['handoffs']} handoffs,"
                f" {value('cost', r['saved'])} saved, {value('cost', net)} net of"
                " rewriting and rereading"
            )
    else:
        each = {
            d: run(con, replace(q, split=(d,)))
            for d in (
                "model",
                "harness",
                "mode",
                "purpose",
                "activity",
                "owner",
                "repo",
                "session",
            )
        }
        daily = run(con, replace(q, by="day"))
        lines = dashboard_lines(a, each, daily, window, names)
    typer.echo("\n".join(lines))


def _subscriptions(root: Path | None) -> dict:
    """The config's usage.subscriptions, or none when it cannot be read."""
    from ..cli import roots_here
    from ..config import load, usage_from
    from ..roots import CONFIG_FILE

    try:
        data, _ = load(roots_here(root).config_root / CONFIG_FILE)
    except Exception:
        return {}
    return usage_from((data or {}).get("usage"))["subscriptions"]


def _local_zone() -> str:
    """The system's IANA zone name, else UTC."""
    try:
        link = Path("/etc/localtime").resolve()
        return str(link).split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"
