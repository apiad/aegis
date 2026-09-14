"""A view renders the brain's planes, not its own copies.

The spec's table is the authority: queues, monitors, canvas and terminals
are brain state, "one copy, all views". They were not. An agent arming a
monitor through MCP reached the brain's MonitorManager while MonitorStrip
rendered the view's, so the tool succeeded and the strip stayed empty.

Asserted on object identity. Equality would pass for two empty managers,
which is exactly the broken state.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.core.planes import BRAIN_PLANES, CONSTRUCTED_PLANES
from aegis.tui.pane import ConversationPane
from aegis.views.registry import ViewRegistry

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _make(p, u, h, **kw):
    return _FakeHarness()


def _world(tmp_path, *, brain=make_brain):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"opus": _agent()}
    mgr = brain(roster, "opus", make_session=_make, mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=_make)
    return mgr, reg


def _pane(app, handle):
    return next(p for p in app.query(ConversationPane) if p.handle == handle)


async def test_a_bridged_view_shares_every_plane(tmp_path):
    mgr, reg = _world(tmp_path)
    view = await reg.open("tty-1", (100, 30))
    try:
        for plane in BRAIN_PLANES + CONSTRUCTED_PLANES:
            brain_side = getattr(mgr, plane)
            assert brain_side is not None, f"the fixture did not wire {plane}"
            assert getattr(view.app, plane) is brain_side, (
                f"{plane}: the view built its own; an agent touching the "
                "brain's would never appear on screen")
    finally:
        await reg.close_all()


async def test_a_brain_missing_a_plane_refuses_the_view(tmp_path):
    """A silent substitute is the bug this file exists for, so a view over
    a brain without a queue manager must not open with one of its own."""
    _, reg = _world(tmp_path, brain=SessionManager)
    with pytest.raises(RuntimeError, match="queue_manager"):
        await reg.open("tty-1", (100, 30))
    await reg.close_all()


async def test_each_view_has_its_own_digest_over_the_brains_queues(
        tmp_path):
    """The digest is derived display state, not a plane. Two views may
    hold two digests; what they must not hold is two queue managers, or
    one view's chips would count a queue the other cannot see."""
    mgr, reg = _world(tmp_path)
    a = await reg.open("tty-a", (100, 30))
    b = await reg.open("tty-b", (100, 30))
    try:
        assert a.app.queue_digest is not b.app.queue_digest
        assert a.app.queue_manager is b.app.queue_manager is mgr.queue_manager
        assert a.app.queue_digest._manager is mgr.queue_manager
    finally:
        await reg.close_all()


async def test_a_monitor_armed_on_the_brain_reaches_the_tab(tmp_path):
    """What Alex saw: `aegis_monitor` succeeded and the strip stayed empty.
    Read through the pane's own monitor manager, which is the object its
    MonitorStrip and sidebar render from."""
    mgr, reg = _world(tmp_path)
    view = await reg.open("tty-1", (100, 30))
    try:
        async with view.app.run_test(headless=False, size=(100, 30)) as p:
            handle = await mgr.spawn("opus")
            await p.pause()
            # Armed on the BRAIN, the way an MCP tool call arrives.
            # autorun=False keeps the poller from running a subprocess; the
            # monitor is still registered, which is what the strip reads.
            mgr.monitor_manager.start_monitor(
                from_handle=handle, description="build",
                done="test -f /nonexistent-on-purpose",
                interval_s=3600.0, timeout_s=3600.0, autorun=False)
            await p.pause()

            seen = _pane(view.app, handle)._monitor_manager.snapshot(
                for_handle=handle)
            assert [m.description for m in seen] == ["build"], (
                "a monitor armed on the brain is invisible to the tab; "
                "this is the strip staying empty")
    finally:
        await reg.close_all()


async def test_closing_a_tab_in_a_view_keeps_the_brains_inbox_binding(
        tmp_path):
    """The inbox is shared now, so a view unbinding on close would cut
    delivery to a session the brain is still running. Binding and unbinding
    belong to the brain, which does both in spawn and close."""
    mgr, reg = _world(tmp_path)
    view = await reg.open("tty-1", (100, 30))
    try:
        async with view.app.run_test(headless=False, size=(100, 30)) as p:
            handle = await mgr.spawn("opus")
            await p.pause()
            await view.app._close_pane(_pane(view.app, handle))
            await p.pause()
            assert mgr.get(handle) is not None
            assert handle in mgr.inbox_router._sessions, (
                "closing a tab in one view unbound the brain's session "
                "from the inbox; its messages would never wake it")
    finally:
        await reg.close_all()


async def test_the_local_path_still_builds_its_own(tmp_path, monkeypatch):
    """With no bridge, AegisApp IS the AppBridge and must keep
    constructing, or every app built directly loses its planes."""
    from aegis.tui.app import AegisApp

    monkeypatch.chdir(tmp_path)
    app = AegisApp({"opus": _agent()}, "opus", _make, FakeMCP())
    for plane in BRAIN_PLANES + CONSTRUCTED_PLANES:
        assert getattr(app, plane) is not None, plane
    assert app.queue_digest._manager is app.queue_manager
