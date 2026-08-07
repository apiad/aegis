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
