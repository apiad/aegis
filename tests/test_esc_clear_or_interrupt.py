"""Esc clears a non-empty input; on an empty input it interrupts the turn."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState
from aegis.tui.widgets import GrowingInput


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
        from aegis.events import Result
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


def _app(session):
    return AegisApp({"default": _agent()}, "default",
                    _factory(session), FakeMCP())


@pytest.mark.asyncio
async def test_esc_clears_nonempty_input_and_does_not_interrupt():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")             # drive into working
        await pilot.pause()
        assert pane.state is AgentState.working
        inp = pane.query_one(GrowingInput)
        inp.value = "half typed"
        inp.focus()

        await pilot.press("escape")
        await pilot.pause()
        assert inp.value == ""                  # cleared
        assert sess.interrupted is False        # turn NOT interrupted
        sess.release()


@pytest.mark.asyncio
async def test_esc_on_empty_input_interrupts_turn():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working
        inp = pane.query_one(GrowingInput)
        inp.value = ""
        inp.focus()

        await pilot.press("escape")
        await pilot.pause()
        assert sess.interrupted is True
        sess.release()
