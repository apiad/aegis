"""ConversationPane: alt/ctrl+enter interrupt-send cuts the live turn and
sends the message as the next turn; idle degrades to a normal enqueue."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pending import PendingStrip
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
        self.started = self.stopped = False
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


def _app(session):
    return AegisApp({"default": _agent()}, "default",
                    _factory(session), FakeMCP())


async def _submit(pane, text, kind="enqueue"):
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text, kind))


@pytest.mark.asyncio
async def test_interrupt_send_while_working_cuts_turn_and_sends():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "first")            # idle → lands, turn blocks
        await pilot.pause()
        assert pane.state is AgentState.working

        await _submit(pane, "urgent", kind="interrupt")
        await pilot.pause()
        # The live turn was interrupted, not queued as a chip.
        assert sess.interrupted is True
        assert not pane.query_one(PendingStrip).chips
        sess.release()


@pytest.mark.asyncio
async def test_interrupt_send_while_idle_is_a_plain_send():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "hello", kind="interrupt")   # idle
        await pilot.pause()
        assert sess.interrupted is False
        assert pane.state is AgentState.working
        sess.release()
