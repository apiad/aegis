"""A daemon view must be able to resume a session.

`plan_resume` skips any tab whose provider is not in the app's driver
registry. `open_view` does not build one, so the registry comes from
whatever `aegis serve` hands `ViewRegistry` -- and that call site was the
one AegisApp construction in cli.py that passed no `drivers`.

The blast radius is everything that resumes, which in the daemon is most
of what the UI is for: restoring your tabs at boot (`_resume_agent_tabs`)
and reopening a conversation from Ctrl+R (`_resume_from_history`). Both
answered "driver-no-resume" for every row, so the daemon looked like it
had lost its history and refused to reopen anything.

A view that cannot resume cannot be a first-class UI, which is what
AGENTS.md claims it is.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result, SystemInit
from aegis.tui.resume_plan import plan_resume
from aegis.state.workspace import Workspace, WorkspaceTab
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    def __init__(self):
        self.sent: list[str] = []

    async def start(self): ...
    async def send(self, t): self.sent.append(t)
    async def close(self): ...

    async def events(self):
        yield SystemInit(session_id="sid-1")
        yield AssistantText("ok", usage=None)
        yield Result(duration_ms=1, is_error=False)


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _registry(tmp_path, **extra):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=lambda p, u, h, **kw: _FakeHarness(),
                       **extra)
    return reg, mgr


async def test_a_view_carries_the_driver_registry(tmp_path):
    """The wiring itself. Asserted on `supports_resume` rather than on the
    dict being non-empty, because what the resume path needs is a driver
    that can actually resume."""
    from aegis.drivers import DRIVERS

    reg, _mgr = _registry(
        tmp_path, drivers={slug: cls() for slug, cls in DRIVERS.items()})
    v = await reg.open("tty-1", (100, 30))
    try:
        assert v.app._drivers, "view app has no driver registry"
        assert any(getattr(d, "supports_resume", False)
                   for d in v.app._drivers.values()), \
            "view app has drivers but none that can resume"
    finally:
        await reg.close_all()


async def test_a_view_without_drivers_can_resume_nothing(tmp_path):
    """The failure this cost us, pinned as a property of plan_resume rather
    than of a UI string: with no registry every tab is skipped, so boot
    restores nothing and Ctrl+R reopens nothing."""
    reg, _mgr = _registry(tmp_path)          # no drivers, the old wiring
    v = await reg.open("tty-1", (100, 30))
    try:
        tab = WorkspaceTab(handle="h", profile="default", order=0,
                           provider="claude-code", session_id="sid-1",
                           created_at="2026-09-12T00:00:00Z", log_id="lg")
        plan = plan_resume(Workspace(tabs=[tab]), v.app._agents,
                           v.app._drivers)
        assert not plan.resumable
        assert [s.reason.value for s in plan.skipped] == ["driver-no-resume"]
    finally:
        await reg.close_all()
