"""AegisApp.restart(handle) cuts the named pane's turn and sends it on.

The companion to test_app_bridge_interrupt.py: stop is interrupt and nothing
else, restart is interrupt plus the one message that puts the agent back to
work.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import RESTART_TEXT
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


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


def _app(sess):
    return AegisApp({"default": _agent()}, "default", _factory(sess), FakeMCP())


@pytest.mark.asyncio
async def test_restart_cuts_the_live_turn_and_sends_continue():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working

        await app.restart(pane.handle)
        await pilot.pause()
        assert sess.interrupted is True
        assert sess.sent[-1] == RESTART_TEXT


@pytest.mark.asyncio
async def test_restart_on_an_idle_pane_only_sends():
    """Nothing to cut, so the interrupt is skipped rather than fired at a
    session that is not in a turn."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        assert pane.state is not AgentState.working

        await app.restart(pane.handle)
        await pilot.pause()
        assert sess.interrupted is False
        assert sess.sent[-1] == RESTART_TEXT


@pytest.mark.asyncio
async def test_restart_unknown_handle_is_noop():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        await app.restart("nobody-here")   # must not raise
        await pilot.pause()
        assert sess.sent == []


@pytest.mark.asyncio
async def test_the_continue_lands_as_a_plain_user_turn():
    """Not tagged `> from …`: the agent is being told to carry on by the
    operator, not handed context by a peer, and a substrate header would
    make a harness read it as an inbox message."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        await app.restart(pane.handle)
        await pilot.pause()
        assert sess.sent[-1] == RESTART_TEXT
        assert "> from" not in sess.sent[-1]


@pytest.mark.asyncio
async def test_stopping_a_background_pane_does_not_steal_the_caret():
    """F10 stops a tab that is not in front. The pane re-enables its input
    when the turn is cut, and focusing it there would pull the caret out of
    whatever tab the operator is actually typing into."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        pane.display = False           # as a background tab
        await pilot.pause()            # let Textual move focus off it first
        focused = app.screen.focused

        await app.interrupt(pane.handle)
        await pilot.pause()
        assert sess.interrupted is True
        assert app.screen.focused is focused
