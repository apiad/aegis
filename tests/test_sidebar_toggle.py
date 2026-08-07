"""F3 toggles a mode, not a widget.

These assertions are on real widget visibility, never on the presence of
the CSS class: a class-name assertion passes against a rule that no longer
hides anything, which is exactly the failure the rule exists to prevent.
The plan's Task 4 step 8 mutation-checks that.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.monitor_strip import MonitorStrip
from aegis.tui.plan_strip import PlanStrip
from aegis.tui.sidebar import Sidebar
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import StatusBar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


COLLAPSED = (QueueStrip, MonitorStrip, PlanStrip, StatusBar)


def _one_task_plan():
    from aegis.plan import PlanState, PlanTask
    return PlanState(tasks=(
        PlanTask(key="1", subject="a", status="in_progress"),))


@pytest.mark.asyncio
async def test_closed_is_todays_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_opening_shows_the_sidebar_and_hides_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()

        assert pane.query_one(Sidebar).display
        still_visible = [w.__class__.__name__
                         for cls in COLLAPSED
                         for w in pane.query(cls) if w.display]
        assert still_visible == []


@pytest.mark.asyncio
async def test_closing_restores_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.toggle_task_dock()
        await pilot.pause()

        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_a_plan_update_cannot_reopen_a_hidden_plan_strip():
    """PlanStrip used to set .display imperatively, and an inline style
    beats CSS — so a plan arriving while the sidebar was open would put the
    strip back on screen underneath it."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.query_one(PlanStrip).refresh_plan(_one_task_plan(), True)
        await pilot.pause()
        assert not pane.query_one(PlanStrip).display


# --- the open mode carries what the collapsed one did -------------------


def _sidebar_text(pane) -> str:
    return pane.query_one(Sidebar).plain()


@pytest.mark.asyncio
async def test_the_open_sidebar_carries_the_status_bar_segments():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 34% ram 61% disk 82%",))
        pane.toggle_task_dock()
        await pilot.pause()
        text = _sidebar_text(pane)
        assert "SESSION" in text
        assert "SYSTEM" in text
        assert "cpu 34%" in text


@pytest.mark.asyncio
async def test_the_open_sidebar_shows_a_monitor_armed_for_this_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        app.monitor_manager.start_monitor(
            from_handle=pane.handle, description="full suite",
            done="false", autorun=False)
        await pilot.pause()
        assert "full suite" in _sidebar_text(pane)


@pytest.mark.asyncio
async def test_a_disconnect_leads_the_session_section():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_connection_state(False)
        pane.toggle_task_dock()
        await pilot.pause()
        lines = [ln for ln in _sidebar_text(pane).split("\n") if ln]
        assert lines[0] == "SESSION"
        assert lines[1].startswith("⚠ disconnected")


@pytest.mark.asyncio
async def test_a_closed_sidebar_stores_but_never_renders():
    """The closed mode costs one branch per event, not a second render
    tree. The model still updates — dropping it would make the first frame
    after a toggle stale — but nothing paints."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        sidebar = pane.query_one(Sidebar)
        sidebar._paints = 0
        pane.set_system(("cpu 99%",))
        pane.refresh_metrics()
        await pilot.pause()
        assert sidebar._paints == 0
        assert sidebar._model.system == ("cpu 99%",)


@pytest.mark.asyncio
async def test_a_closed_pane_releases_its_sidebar_subscriptions():
    """The sidebar subscribes to the monitor manager and the queue digest,
    both of which outlive any one pane. Without a matching release the
    callbacks pile up and fire into a torn-down pane — which surfaced as
    `RuntimeError: Event loop is closed` at interpreter shutdown, and is
    the same shape as the watchdog-observer leak that once ate the user's
    inotify instances."""
    app = _app()
    async with app.run_test() as pilot:
        before = len(app.monitor_manager._subs)
        handle = await app.spawn("default", handle="peer-one")
        await pilot.pause()
        assert len(app.monitor_manager._subs) > before

        await app.close(handle)
        await pilot.pause()
        assert len(app.monitor_manager._subs) == before


@pytest.mark.asyncio
async def test_the_first_frame_after_opening_is_not_stale():
    """The corollary: an update that arrived while closed must be on
    screen the moment it opens."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 99%",))
        pane.toggle_task_dock()
        await pilot.pause()
        assert "cpu 99%" in _sidebar_text(pane)
