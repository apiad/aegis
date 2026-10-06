"""ConversationPane: a `!command` input runs the shell and delivers the
output to the agent as a user message (rendered in the transcript)."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self._gate = asyncio.Event()

    async def start(self):
        pass

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        pass

    async def close(self):
        pass

    def release(self):
        self._gate.set()


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def _app(session):
    def make(agent, mcp_url, handle):
        return session
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


async def _submit(pane, text, kind="enqueue"):
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text, kind))


@pytest.mark.asyncio
async def test_bang_runs_shell_and_delivers_output():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "!echo aegistest")
        await pilot.pause()
        # The command output was sent to the agent as its turn text.
        assert sess.sent, "no turn was started"
        turn = sess.sent[0]
        assert "$ echo aegistest" in turn
        assert "aegistest" in turn
        sess.release()


@pytest.mark.asyncio
async def test_bare_bang_is_ignored():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "!")
        await pilot.pause()
        assert not sess.sent
        sess.release()
