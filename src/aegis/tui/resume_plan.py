"""Pure classification: which tabs in a workspace can be resumed?

The TUI bootstrap calls plan_resume(workspace, agents, drivers), opens
the resumable ones via driver.resume(), and reports skipped ones in a
single startup-banner line.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aegis.state.workspace import Workspace, WorkspaceTab


class SkipReason(str, Enum):
    profile_missing = "profile-missing"
    driver_no_resume = "driver-no-resume"
    no_session_id = "no-session-id"


@dataclass(frozen=True)
class TabPlan:
    tab: WorkspaceTab


@dataclass(frozen=True)
class SkippedTab:
    tab: WorkspaceTab
    reason: SkipReason


@dataclass(frozen=True)
class ResumePlan:
    resumable: list[TabPlan]
    skipped: list[SkippedTab]


def plan_resume(ws: Workspace, agents: dict, drivers: dict) -> ResumePlan:
    resumable: list[TabPlan] = []
    skipped: list[SkippedTab] = []
    for tab in sorted(ws.tabs, key=lambda t: t.order):
        if tab.profile not in agents:
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.profile_missing))
            continue
        drv = drivers.get(tab.provider)
        if drv is None or not getattr(drv, "supports_resume", False):
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.driver_no_resume))
            continue
        if not tab.session_id:
            skipped.append(SkippedTab(tab=tab, reason=SkipReason.no_session_id))
            continue
        resumable.append(TabPlan(tab=tab))
    return ResumePlan(resumable=resumable, skipped=skipped)
