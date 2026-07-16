"""ConversationPane toggles a `working` class while the agent is mid-turn, so
the input outline can signal idle (live, acts now) vs working (message queues)."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = False
        self._gate = asyncio.Event()

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def close(self):
        self.closed = True

    def release(self):
        self._gate.set()


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        pass

    async def stop(self):
        pass


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


def _app(session):
    return AegisApp({"default": _agent()}, "default",
                    _factory(session), FakeMCP())


@pytest.mark.asyncio
async def test_pane_toggles_working_class_across_the_turn():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        assert not pane.has_class("working")     # idle at rest

        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working
        assert pane.has_class("working")         # busy → subdued outline

        sess.release()
        await pilot.pause()
        assert pane.state is AgentState.ready
        assert not pane.has_class("working")     # back to vivid idle outline
