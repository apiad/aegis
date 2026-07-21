"""Working-indicator lifecycle: cleared on interrupt, re-animates on the next
turn; and the compact 'thought' summary for reasoning blocks."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import _thought_summary
from aegis.tui.state import AgentState
from aegis.tui.themes import INK, aegis_colors
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

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        ...

    async def stop(self):
        ...


def _app(session):
    def make(agent, mcp_url, handle):
        return session
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


async def _submit(pane, text, kind="enqueue"):
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text, kind))


# --- pure: the thought summary ----------------------------------------

def test_thought_summary_is_a_compact_line():
    out = _thought_summary(42.0, 4800, aegis_colors(INK)).plain
    assert "thought" in out
    assert "42.0s" in out          # elapsed
    assert "1.2k tok" in out       # 4800 chars // 4 ≈ 1200 tokens


# --- lifecycle: Bug 1 (cleared on interrupt) --------------------------

@pytest.mark.asyncio
async def test_indicator_clears_on_interrupt():
    sess = GatedSession()
    async with _app(sess).run_test() as pilot:
        pane = pilot.app._panes[0]
        await _submit(pane, "go")
        await pilot.pause()
        assert pane.state is AgentState.working
        ind = pane._working_indicator()
        assert ind is not None and ind.is_active

        pane.interrupt()                       # Escape path
        await pilot.pause()
        await pilot.pause()
        # Core emits (ready, finished=False); the indicator must still go away.
        assert pane.state is AgentState.ready
        assert pane._working_indicator() is None
        sess.release()


# --- lifecycle: Bug 2 (re-animates on the next turn) ------------------

@pytest.mark.asyncio
async def test_indicator_reappears_live_on_next_turn():
    sess = GatedSession()
    async with _app(sess).run_test() as pilot:
        pane = pilot.app._panes[0]
        await _submit(pane, "one")
        await pilot.pause()
        pane.interrupt()
        await pilot.pause()
        await pilot.pause()
        assert pane._working_indicator() is None

        await _submit(pane, "two")             # fresh turn
        await pilot.pause()
        ind = pane._working_indicator()
        assert ind is not None and ind.is_active   # live, not frozen
        sess.release()
