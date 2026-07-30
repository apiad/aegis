"""The per-event path must not walk the transcript.

`self.query(X)` is a deep, uncached CSS walk over every descendant of the
pane — and a pane's descendants are its transcript blocks. Using it to find
a widget that never moves made the cost of handling one streamed token grow
with how long you had been in the tab (3.3 ms empty → 36 ms at the eviction
cap, measured). These tests assert the *structure* — that the hot path does
no deep query at all — rather than wall-clock timings, which flake on a
loaded box.
"""
from __future__ import annotations

import pytest
from rich.text import Text

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane, WorkingIndicator
from aegis.tui.widgets import StatusBar


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("hi")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda *a, **kw: FakeSession(), FakeMCP())


class _QueryCounter:
    """Shadows the instance's `query` so a deep walk can't hide."""

    def __init__(self, pane: ConversationPane) -> None:
        self.calls: list[object] = []
        self._real = pane.query
        pane.query = self                     # instance attr shadows method

    def __call__(self, selector=None):
        self.calls.append(selector)
        return self._real(selector)


@pytest.mark.asyncio
async def test_refresh_metrics_does_not_query_the_subtree():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(30):
            pane._mount_block(Text(f"block {i}"), f"block {i}")
        await pilot.pause()

        counter = _QueryCounter(pane)
        pane.refresh_metrics()
        pane.set_system(())
        pane.set_quota(())
        assert counter.calls == []


@pytest.mark.asyncio
async def test_working_indicator_does_not_query_the_subtree():
    """It is mounted lazily on the first turn and removed when the turn
    ends, so the cache has to follow that lifecycle — not just memoize."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        assert pane._working_indicator() is None      # none mounted yet

        pane._start_indicator()
        await pilot.pause()
        counter = _QueryCounter(pane)
        assert isinstance(pane._working_indicator(), WorkingIndicator)
        for i in range(10):                           # the _mount_block path
            pane._mount_block(Text(f"b{i}"), f"b{i}")
        assert counter.calls == []

        pane._stop_indicator()
        await pilot.pause()
        assert pane._working_indicator() is None      # not a stale reference


@pytest.mark.asyncio
async def test_ingesting_an_event_does_not_query_the_subtree():
    """The whole `_on_core_event` path, not just the pieces."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(20):
            pane._mount_block(Text(f"block {i}"), f"block {i}")
        await pilot.pause()

        counter = _QueryCounter(pane)
        pane._on_core_event(pane._core, AssistantText("streamed token"))
        pane._on_core_event(pane._core, Result(duration_ms=1, is_error=False))
        await pilot.pause()
        assert counter.calls == []


@pytest.mark.asyncio
async def test_status_bar_reference_survives_and_still_updates():
    """Caching must not break the thing it caches."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        bar = pane.query_one(StatusBar)
        pane.refresh_metrics()
        pane.set_system(("sys",))
        pane.set_quota(("quota",))
        await pilot.pause()
        assert pane._bar() is bar


@pytest.mark.asyncio
async def test_metrics_refresh_is_a_noop_before_the_bar_mounts():
    """Core observers can fire before compose finishes; that was the
    original reason for the `if bars:` guard and it still has to hold."""
    pane = ConversationPane(
        FakeSession(), _agent(), "default", "unmounted-pane",
        __import__("aegis.tui.themes", fromlist=["aegis_colors"]).aegis_colors(
            __import__("aegis.tui.themes", fromlist=["INK"]).INK))
    pane.refresh_metrics()          # must not raise
    assert pane._bar() is None
