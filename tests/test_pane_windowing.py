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
async def test_eviction_caps_mounted_widget_count():
    """Once history exceeds N_MAX and user is at the bottom, eviction
    keeps the mounted CopyableBlock count bounded."""
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Pump enough non-streaming events to exceed N_MAX.
        for i in range(N_MAX + 80):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        assert len(pane._history) == N_MAX + 80
        mounted = len(pane.query(CopyableBlock))
        assert mounted <= N_MAX
        # _window_start advanced past the first eviction.
        assert pane._window_start >= 50


@pytest.mark.asyncio
async def test_no_eviction_while_user_scrolled_up():
    """User reading old content does not get yanked when new events arrive."""
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fill close to but under N_MAX so no eviction has run yet.
        for i in range(N_MAX - 10):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"a{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        # Scroll up.
        t = pane.query_one("#transcript", VerticalScroll)
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False
        start_before = pane._window_start
        # Pump more events that, with sticky=True, would have triggered eviction.
        for i in range(50):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"b{i}.py", kind="read"))
        await pilot.pause()
        # No eviction happened — user's scroll position protected.
        assert pane._window_start == start_before


@pytest.mark.asyncio
async def test_sticky_bottom_flag_starts_true_and_flips_on_scroll_up():
    """Pane starts sticky; scrolling away from the bottom flips the flag."""
    from textual.containers import VerticalScroll
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fresh pane is at scroll_y=0 == max_scroll_y=0 → sticky.
        assert pane._stick_to_bottom is True

        # Pump enough events to make the transcript scrollable.
        for i in range(60):
            pane._on_core_event(
                None, ToolUse(name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()

        t = pane.query_one("#transcript", VerticalScroll)
        # Scroll to top.
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False

        # Scroll back to bottom.
        t.scroll_y = t.max_scroll_y
        await pilot.pause()
        assert pane._stick_to_bottom is True


@pytest.mark.asyncio
async def test_streaming_updates_history_record_in_place():
    """Three streamed AssistantText chunks coalesce into one widget AND
    one BlockRecord whose payload reflects the full concatenated text."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hel", usage=None))
        pane._on_core_event(None, AssistantText(text="lo ", usage=None))
        pane._on_core_event(None, AssistantText(text="world", usage=None))
        assert len(pane._history) == 1
        assert pane._history[0].payload == "hello world"


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
