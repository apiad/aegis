"""AegisApp.interrupt(handle) cuts the named pane's live turn."""
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
        self.started = self.closed = self.interrupted = False
        self._gate = asyncio.Event()

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        self.interrupted = True

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


@pytest.mark.asyncio
async def test_app_interrupt_by_handle_cuts_that_pane():
    sess = GatedSession()
    app = AegisApp({"default": _agent()}, "default",
                   _factory(sess), FakeMCP())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working

        await app.interrupt(pane.handle)
        await pilot.pause()
        assert sess.interrupted is True


@pytest.mark.asyncio
async def test_app_interrupt_unknown_handle_is_noop():
    sess = GatedSession()
    app = AegisApp({"default": _agent()}, "default",
                   _factory(sess), FakeMCP())
    async with app.run_test() as pilot:
        await app.interrupt("nobody-here")   # must not raise
        await pilot.pause()
