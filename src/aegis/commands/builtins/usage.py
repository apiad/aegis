"""``/usage`` — session usage & cost analytics, rendered as a transcript
block. Reuses the same engine + renderer as the ``aegis usage`` CLI, so the
TUI and web client show identical data. Read-only.

    /usage                     dashboard (cost, averages, models, tools, top)
    /usage tools               tool → cost correlation
    /usage sessions            cost-per-session distribution + top 15
    /usage month|dow|hour      turns bucketed over time (local timezone)
"""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec
from aegis.usage import build_report
from aegis.usage.env import default_agent, state_dir
from aegis.usage.render import (
    _money, dashboard_lines, sessions_lines, temporal_lines, tools_lines,
)

_VIEWS = ("dashboard", "tools", "sessions", "month", "dow", "hour")


async def _usage(ctx: CommandContext, args) -> CommandResult:
    view = args.get("view") or "dashboard"
    if view not in _VIEWS:
        return CommandResult(False, f"unknown view: {view}",
                             "views: " + ", ".join(_VIEWS[1:]))
    dmodel, dprovider = default_agent()
    report = build_report(state_dir(), default_model=dmodel,
                          default_provider=dprovider)
    if not report.sessions:
        return CommandResult(True, "no session logs found")
    if view == "dashboard":
        lines = dashboard_lines(report)
        title = (f"usage · {len(report.sessions)} sessions · "
                 f"{_money(report.total_billed())} billed")
    elif view == "tools":
        lines, title = tools_lines(report), "usage · tools"
    elif view == "sessions":
        lines, title = sessions_lines(report), "usage · sessions"
    else:  # month | dow | hour
        lines, title = temporal_lines(report, view), f"usage · by {view}"
    return CommandResult(True, title, "\n".join(lines))


register(SlashCommand(
    "usage", "session cost & token analytics", "/usage [view]", _usage,
    spec=ArgSpec(positionals=(
        Arg("view", required=False, completer=_VIEWS),))))
