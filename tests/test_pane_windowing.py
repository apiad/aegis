"""Transcript windowing: bounded mounted widget count, scroll-up reloads."""
import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.pane import CopyableBlock


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self):
        self.started = self.stopped = False
        self.bound = None
    def bind(self, bridge): self.bound = bridge
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    def make(agent, mcp_url, handle):
        try:
            return next(it)
        except StopIteration:
            return FakeSession()
    return make


def _app():
    return AegisApp({"default": _agent()}, "default",
                    _factory(), FakeMCP())


@pytest.mark.asyncio
async def test_history_records_every_event():
    """Every rendered event appends a BlockRecord to _history."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hello", usage=None))
        pane._on_core_event(None, ToolUse(name="Read", summary="x.py", kind="read"))
        pane._on_core_event(None, Result(duration_ms=1, is_error=False))
        # Streaming text + tool use + result → 3 records.
        assert len(pane._history) == 3
        # Each record has a renderable and a payload string.
        for rec in pane._history:
            assert rec.renderable is not None
            assert isinstance(rec.payload, str)
