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
from aegis.usage.quota_providers import PROVIDERS
from aegis.usage.render import (
    _money, dashboard_lines, sessions_lines, temporal_lines, tools_lines,
)

_VIEWS = ("dashboard", "tools", "sessions", "month", "dow", "hour", "quota")

_SERVICES = None


def _quota_services(ctx):
    """The app's per-provider QuotaServices, else a private set.

    ``/usage quota`` must work headlessly (web client, ``aegis serve``) where no
    TUI app owns them, so fall back to module-level ones.
    """
    global _SERVICES
    services = getattr(getattr(ctx, "bridge", None), "quota_services", None)
    if services is not None:
        return services
    if _SERVICES is None:
        from aegis.usage.quota_providers import build_services
        _SERVICES = build_services()
    return _SERVICES


def _worst(state):
    """The window closest to exhaustion, for the title.

    Every window, not just the ones the status bar has room for — this view's
    body lists them all, so its headline must not summarise a subset. Claude's
    ``weekly_opus`` in particular never reaches the bar and can still be the
    binding constraint.
    """
    if state.snapshot is None or not state.snapshot.windows:
        return None
    return max(w.percent for w in state.snapshot.windows)


async def _usage(ctx: CommandContext, args) -> CommandResult:
    view = args.get("view") or "dashboard"
    if view not in _VIEWS:
        return CommandResult(False, f"unknown view: {view}",
                             "views: " + ", ".join(_VIEWS[1:]))
    if view == "quota":
        import asyncio

        from aegis.usage.quota import quota_report
        services = _quota_services(ctx)
        providers = [p for p in PROVIDERS if p.name in services]
        await asyncio.gather(*(services[p.name].refresh(force=True)
                               for p in providers))
        readings = [(p, services[p.name].current()) for p in providers]
        summary = [(p, _worst(s)) for p, s in readings]
        summary = [(p, w) for p, w in summary if w is not None]
        title = "usage · quota"
        if len(summary) == 1:
            title += f" · {summary[0][1]:.0f}%"
        elif summary:
            title += " · " + " · ".join(
                f"{p.label} {w:.0f}%" for p, w in summary)
        return CommandResult(True, title, "\n".join(quota_report(readings)))
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
