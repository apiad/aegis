"""ConversationPane toggles a `shell-escape` class while the input starts
with `!`, so the outline turns magenta to flag a shell escape vs a normal
message to the agent."""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.tui.app import AegisApp
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class DummySession:
    async def start(self):
        pass

    async def send(self, text):
        pass

    async def events(self):
        return
        yield  # pragma: no cover — make this an async generator

    async def close(self):
        pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def _app():
    def make(agent, mcp_url, handle):
        return DummySession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


@pytest.mark.asyncio
async def test_bang_prefix_toggles_shell_escape_class():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        inp = pane.query_one(GrowingInput)
        assert not pane.has_class("shell-escape")   # plain input at rest

        inp.value = "!ls -la"
        await pilot.pause()
        assert pane.has_class("shell-escape")        # `!` → magenta outline

        inp.value = "just a message"
        await pilot.pause()
        assert not pane.has_class("shell-escape")    # back to normal

        inp.value = "!echo hi"
        await pilot.pause()
        assert pane.has_class("shell-escape")
        inp.value = ""                               # clears on submit-empty
        await pilot.pause()
        assert not pane.has_class("shell-escape")
