"""``aegis usage`` — session usage & cost analytics (read-only).

Thin typer surface over the shared aggregation engine (``aegis.usage``) and
renderer (``aegis.usage.render``); the ``/usage`` slash command reuses the
same two.
"""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from aegis.config import find_project_root
from aegis.cost import CostOptions, measure, sweep
from aegis.cost.locality import SPLIT_DIRS
from aegis.cost.measure import write_cache
from aegis.cost.render import repo_lines, sweep_lines
from aegis.usage import build_report
from aegis.usage.env import default_agent, state_dir
from aegis.usage.quota import quota_report
from aegis.usage.quota_providers import build_services, read_all
from aegis.usage.render import (
    dashboard_lines,
    sessions_lines,
    temporal_lines,
    tools_lines,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.command("quota")
def quota() -> None:
    """Live subscription quota for every provider you have credentials for."""
    import asyncio

    readings = asyncio.run(read_all(build_services()))
    typer.echo("\n".join(quota_report(readings)))


def _cost_options(
    since: str | None,
    until: str | None,
    no_foreign: bool,
    exclude: list[str] | None,
    split: str | None,
    extra_root: list[str] | None,
    state: str | None,
) -> CostOptions:
    root = find_project_root() or Path.cwd()
    named_splits = frozenset(s for s in (split or "").split(",") if s)
    return CostOptions(
        since=since,
        until=until,
        split_dirs=named_splits or SPLIT_DIRS,
        exclude=tuple(exclude or ()),
        foreign=not no_foreign,
        extra_roots=tuple(Path(p) for p in (extra_root or ())),
        state_dir=Path(state) if state else state_dir(root),
    )


@app.command("repo")
def repo_cost(
    path: str = typer.Argument(..., help="path to the git repo to measure"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    until: str = typer.Option(None, "--until", help="ISO date upper bound"),
    no_foreign: bool = typer.Option(
        False, "--no-foreign", help="skip ~/.claude/projects"
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", help="glob dropped from the git side (repeatable)"
    ),
    split: str = typer.Option(
        None, "--split", help="comma-separated monorepo containers"
    ),
    extra_root: list[str] = typer.Option(
        None, "--extra-root", help="another directory of claude transcripts"
    ),
    state: str = typer.Option(None, "--state", help="override the aegis state dir"),
    as_json: bool = typer.Option(False, "--json", help="print the full structure"),
) -> None:
    """What one repository cost to build, measured against its transcripts."""
    options = _cost_options(since, until, no_foreign, exclude, split, extra_root, state)
    target = Path(path)
    if not (target / ".git").exists():
        typer.echo(f"not a git repo: {target}")
        raise typer.Exit(2)
    result = measure(target, options)
    if as_json:
        # Only --json caches, as documented. Writing on every run would let a
        # windowed table run silently replace the figure aegis_repo_cost serves,
        # with nothing in the payload marking it as windowed.
        if options.state_dir:
            write_cache(options.state_dir, result)
        typer.echo(json.dumps(result.to_dict(), indent=1, default=str))
    else:
        typer.echo("\n".join(repo_lines(result)))


@app.command("repos")
def repos_cost(
    directory: str = typer.Argument(..., help="directory of git repos to sweep"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    until: str = typer.Option(None, "--until", help="ISO date upper bound"),
    no_foreign: bool = typer.Option(
        False, "--no-foreign", help="skip ~/.claude/projects"
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", help="glob dropped from the git side (repeatable)"
    ),
    split: str = typer.Option(
        None, "--split", help="comma-separated monorepo containers"
    ),
    extra_root: list[str] = typer.Option(
        None, "--extra-root", help="another directory of claude transcripts"
    ),
    state: str = typer.Option(None, "--state", help="override the aegis state dir"),
    as_json: bool = typer.Option(False, "--json", help="print the full structure"),
) -> None:
    """Every git repo directly under a directory, measured in one sweep."""
    options = _cost_options(since, until, no_foreign, exclude, split, extra_root, state)
    target = Path(directory)
    if not target.is_dir():
        typer.echo(f"not a directory: {target}")
        raise typer.Exit(2)
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
) -> None:
    # invoke_without_command=True means this callback also fires for a
    # subcommand — without this guard `aegis usage quota` would print the cost
    # dashboard too.
    if ctx.invoked_subcommand:
        return
    zone = ZoneInfo(tz) if tz else None
    # A CLI entrypoint: the invocation directory *is* the input, which is
    # exactly what the rest of `aegis` does at its command boundaries.
    root = find_project_root() or Path.cwd()
    dmodel, dprovider = default_agent(root)
    report = build_report(
        state_dir(root),
        default_model=dmodel,
        default_provider=dprovider,
        since=since,
        handle=session,
    )
    if model:
        report.sessions = [s for s in report.sessions if s.model == model]
    if not report.sessions:
        typer.echo("No session logs found.")
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
