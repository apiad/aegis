"""ConversationPane: text-box input queues as click-to-dequeue chips while
the agent is working, and drains to user lines at the turn boundary."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane
from aegis.tui.pending import Chip, PendingStrip
from aegis.tui.state import AgentState
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    """Harness whose turn blocks on a gate until released, so the pane
    stays in the working state while we queue follow-up input."""

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


async def _submit(pane: ConversationPane, text: str) -> None:
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text))


def _user_lines(pane: ConversationPane) -> list[str]:
    return [r.payload for r in pane._history if r.payload]


@pytest.mark.asyncio
async def test_submit_while_working_queues_a_chip():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "first")      # idle → lands, turn blocks on gate
        await pilot.pause()
        assert pane.state is AgentState.working

        await _submit(pane, "second")     # working → queued chip
        await _submit(pane, "third")
        await pilot.pause()
        strip = pane.query_one(PendingStrip)
        assert [c.msg.body for c in strip.chips] == ["second", "third"]
        # input is NOT disabled while working
        assert pane.query_one(GrowingInput).disabled is False
        sess.release()


@pytest.mark.asyncio
async def test_dequeue_chip_cancels_before_dispatch():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "first")
        await pilot.pause()
        await _submit(pane, "second")
        await _submit(pane, "third")
        await pilot.pause()
        strip = pane.query_one(PendingStrip)
        third_chip = strip.chips[1]
        pane.on_chip_dequeued(Chip.Dequeued(third_chip, third_chip.msg))
        await pilot.pause()
        assert [c.msg.body for c in strip.chips] == ["second"]

        sess.release()                    # drain the chained turn
        await pilot.pause()
        await pilot.pause()
        # only "second" reached the harness on the chained turn; "third"
        # was cancelled and never sent.
        assert "second" in sess.sent[1]
        assert all("third" not in s for s in sess.sent)


@pytest.mark.asyncio
async def test_dispatch_clears_chips_and_mounts_user_lines():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "first")
        await pilot.pause()
        await _submit(pane, "queued")
        await pilot.pause()
        assert len(pane.query_one(PendingStrip).chips) == 1

        sess.release()
        await pilot.pause()
        await pilot.pause()
        strip = pane.query_one(PendingStrip)
        assert list(strip.chips) == [] and strip.has_class("-empty")
        # both the landed and the chained user message rendered as lines
        assert "first" in _user_lines(pane)
        assert "queued" in _user_lines(pane)
