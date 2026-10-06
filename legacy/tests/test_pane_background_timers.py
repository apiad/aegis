"""Background panes must not run their 10 Hz visual timers.

A hidden tab's WorkingIndicator animation and per-tool spinner are purely
cosmetic — nobody is looking at them. Leaving them ticking at 100 ms in
every backgrounded working pane piles tab-proportional refresh work onto
Textual's single message pump, competing with the active tab's rendering
(the "slow with multiple tabs" symptom). The timers pause while hidden and
resume on show; the data model stays current either way.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(agent, mcp_url, handle):
    return FakeSession()


def _drive_working_with_tool(pane):
    pane._on_core_state(None, AgentState.working, False)
    pane._on_core_event(None, ToolUse(
        name="Read", summary="x.py", kind="read", tool_call_id="T1"))


@pytest.mark.asyncio
async def test_hidden_working_pane_freezes_its_timers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.spawn("default", handle="worker")
        await pilot.pause()
        await pilot.pause()
        worker = app._panes[1]
        assert worker.display is False

        # A hidden pane starts a working turn with a running tool.
        _drive_working_with_tool(worker)
        await pilot.pause()

        ind = worker._working_indicator()
        assert ind is not None and ind.is_active  # state tracked...
        assert ind._tick_timer is None            # ...but not animating
        assert worker._tool_timer is None         # no spinner ticks

        # Activating the tab resumes the animation.
        app._activate(1)
        await pilot.pause()
        assert worker.display is True
        ind = worker._working_indicator()
        assert ind is not None and ind._tick_timer is not None
        assert worker._tool_timer is not None

        # Switching away pauses it again.
        app._activate(0)
        await pilot.pause()
        ind = worker._working_indicator()
        assert ind is not None and ind._tick_timer is None
        assert worker._tool_timer is None
