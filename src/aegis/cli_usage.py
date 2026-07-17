"""``aegis usage`` — session usage & cost analytics (read-only).

Thin typer surface over the shared aggregation engine (``aegis.usage``) and
renderer (``aegis.usage.render``); the ``/usage`` slash command reuses the
same two.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import typer

from aegis.usage import build_report
from aegis.usage.env import default_agent, state_dir
from aegis.usage.render import (
    dashboard_lines, sessions_lines, temporal_lines, tools_lines,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.callback(invoke_without_command=True)
def usage(
    by: str = typer.Option(None, "--by", help="month|dow|hour"),
    sessions: bool = typer.Option(False, "--sessions",
                                  help="cost distribution + top sessions"),
    tools: bool = typer.Option(False, "--tools",
                               help="tool→cost correlation"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    session: str = typer.Option(None, "--session", help="single handle"),
    model: str = typer.Option(None, "--model", help="filter to one model"),
    tz: str = typer.Option(None, "--tz", help="IANA tz (default: system)"),
) -> None:
    zone = ZoneInfo(tz) if tz else None
    dmodel, dprovider = default_agent()
    report = build_report(state_dir(), default_model=dmodel,
                          default_provider=dprovider, since=since,
                          handle=session)
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
