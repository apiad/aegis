"""``aegis usage``: what the agents' work cost, read from aegis's own store.

The bare command is the dashboard (``report.py``); ``repo`` and ``repos`` price
a git repository's agent work against every transcript store (``measure.py``).
Every path comes from the roots the top-level CLI builds (``cli.roots_here``),
the one place that reads the working directory.
"""

from __future__ import annotations

import json
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


@app.callback(invoke_without_command=True)
def usage(
    ctx: typer.Context,
    by: str = typer.Option(None, "--by", help="month|dow|hour"),
    sessions: bool = typer.Option(
        False, "--sessions", help="cost distribution + top sessions"
    ),
    tools: bool = typer.Option(False, "--tools", help="tool→cost correlation"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    session: str = typer.Option(None, "--session", help="single handle"),
    model: str = typer.Option(None, "--model", help="filter to one model"),
    tz: str = typer.Option(None, "--tz", help="IANA tz (default: system)"),
    state: str = STATE,
    root: Path | None = ROOT,
) -> None:
    """Session usage and cost, from aegis's own store."""
    # The callback fires for a subcommand too; without this guard
    # `aegis usage repo` would print the dashboard first.
    if ctx.invoked_subcommand:
        return
    from zoneinfo import ZoneInfo

    from .render import dashboard_lines, sessions_lines, temporal_lines, tools_lines
    from .report import build_report

    if by and by not in ("month", "dow", "hour"):
        typer.echo(f"--by takes month, dow or hour, not {by!r}")
        raise typer.Exit(2)
    zone = ZoneInfo(tz) if tz else None
    report = build_report(_state(state, root), since=since, handle=session)
    if model:
        report.sessions = [s for s in report.sessions if s.model == model]
    if not report.sessions:
        typer.echo("No sessions found.")
        raise typer.Exit(0)
    if by:
        lines = temporal_lines(report, by, zone)
    elif sessions:
        lines = sessions_lines(report)
    elif tools:
        lines = tools_lines(report)
    else:
        lines = dashboard_lines(report, zone)
    typer.echo("\n".join(lines))
