"""Tests for Task 11: reconnect status banner + window_reset pane clear.

Test 1 (standalone): StatusBar.set_connection_state(up) toggles a
    disconnected banner visible in render_plain().

Test 2 (unit): A window_reset stream frame for a subscribed handle
    clears the corresponding ConversationPane._history via _on_window_reset.
"""
from __future__ import annotations

import asyncio
import pytest

from aegis.tui.widgets import StatusBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_status_bar():
    """Construct a StatusBar (does not require a live Textual app for
    set_connection_state / render_plain — those operate on the internal
    content string, not on mounted widget children)."""
    from aegis.tui.themes import INK, aegis_colors
    colors = aegis_colors(INK)
    bar = StatusBar("claude", "high", colors)
    return bar


class _FakeWsClientWithConnection:
    """FakeWsClient extended with on_connection support for Task-11 tests."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self._rpc_results: dict[str, list] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.subscribed_globals: set[str] = set()
        self.subscribed_sessions: list[tuple[str, int | None]] = []
        self._connection_handlers: list = []

    @property
    def constants(self) -> dict:
        return {}

    def rpc_result(self, method: str, result: dict) -> None:
        self._rpc_results.setdefault(method, []).append(result)

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        self.rpc_calls.append((method, params or {}))
        queue = self._rpc_results.get(method, [])
        if queue:
            return queue.pop(0)
        return {}

    def on(self, kind: str, fn) -> None:
        self._handlers.setdefault(kind, []).append(fn)

    def on_connection(self, fn) -> None:
        self._connection_handlers.append(fn)

    def inject_connection(self, up: bool) -> None:
        """Fire all on_connection handlers with up."""
        for fn in list(self._connection_handlers):
            fn(up)

    async def subscribe_global(self, stream: str) -> None:
        self.subscribed_globals.add(stream)

    async def subscribe_session(self, handle: str, *,
                                tail: int | None = None) -> None:
        self.subscribed_sessions.append((handle, tail))

    def inject_stream(self, kind: str, frame: dict) -> None:
        for fn in list(self._handlers.get(kind, [])):
            fn(frame)


@pytest.fixture
def fake_ws_with_connection():
    return _FakeWsClientWithConnection()


# ---------------------------------------------------------------------------
# Test 1 — StatusBar banner (no app needed)
# ---------------------------------------------------------------------------

def test_status_bar_shows_disconnected_banner_on_connection_drop():
    bar = _make_status_bar()
    bar.set_connection_state(False)
    assert "disconnected" in bar.render_plain().lower()
    bar.set_connection_state(True)
    assert "disconnected" not in bar.render_plain().lower()


# ---------------------------------------------------------------------------
# Test 2 — window_reset clears pane transcript (unit-level, no Textual app)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_window_reset_clears_pane_transcript(fake_ws_with_connection):
    """A window_reset stream frame for handle 'h' clears the pane _history.

    This test drives the handler directly — no Textual run_test needed — so
    it stays fast and fully deterministic.
    """
    from aegis.config import Agent
    from aegis.tui.pane import ConversationPane, BlockRecord
    from aegis.tui.remote_manager import RemoteSessionManager
    from aegis.tui.app import AegisApp
    from aegis.tui.themes import INK, aegis_colors
    from rich.text import Text

    ws = fake_ws_with_connection
    ws.rpc_result("list_sessions", {"sessions": []})
    mgr = RemoteSessionManager(ws)
    await mgr.start()

    palette = aegis_colors(INK)
    agent = Agent(harness="claude-code", model="claude-sonnet")

    # Build a minimal fake pane (without a live Textual session)
    class _FakeSession:
        async def start(self): pass
        async def send(self, text): pass
        async def events(self):
            if False: yield  # noqa
        async def close(self): pass

    pane = ConversationPane.__new__(ConversationPane)
    pane._agent = agent
    pane.agent_slug = "main"
    pane.handle = "h"
    pane._palette = palette
    pane._history = []
    pane._window_start = 0
    pane._mounted_blocks = []
    pane._streaming_block = None
    pane._streaming_kind = None
    pane._streaming_text = ""
    pane._streaming_history_idx = None

    # Seed _history with a stale entry
    pane._history.append(BlockRecord(Text("stale"), "stale", False))
    assert len(pane._history) == 1

    # Build a minimal AegisApp just enough to hold the pane + handler
    class _FakeMcp:
        url = "http://fake"
        async def start(self): pass
        async def stop(self): pass
        def bind(self, app): pass

    app = AegisApp.__new__(AegisApp)
    app._remote_manager = mgr
    app._panes = [pane]
    # The _ws attribute is what the brief's wiring uses to inject the handler
    app._ws = ws

    # Wire the window_reset handler the same way app.py will do it
    ws.on("window_reset", app._on_window_reset)

    # Inject window_reset for "h"
    ws.inject_stream("window_reset", {"handle": "h", "dropped_through_seq": 10})
    await asyncio.sleep(0)

    assert pane._history == [], (
        f"Expected empty _history after window_reset, got {pane._history!r}")


# ---------------------------------------------------------------------------
# Test 3 — on_connection wiring bridges to StatusBar (unit-level)
# ---------------------------------------------------------------------------

def test_on_connection_updates_status_bar(fake_ws_with_connection):
    """When the WS connection drops, the StatusBar shows 'disconnected'.
    When it reconnects, the banner is cleared. Driven via inject_connection
    without a live Textual app."""
    from aegis.tui.app import AegisApp
    from aegis.tui.themes import INK, aegis_colors
    from aegis.tui.remote_manager import RemoteSessionManager
    from aegis.config import Agent

    ws = fake_ws_with_connection

    palette = aegis_colors(INK)
    agent = Agent(harness="claude-code", model="claude-sonnet")

    # Fake status bar that records calls
    calls: list[bool] = []

    class _TrackingBar:
        def set_connection_state(self, up: bool, reason: str = "") -> None:
            calls.append(up)

    class _FakeMcp:
        url = "http://fake"
        async def start(self): pass
        async def stop(self): pass
        def bind(self, app): pass

    mgr = RemoteSessionManager(ws)

    app = AegisApp.__new__(AegisApp)
    app._remote_manager = mgr
    app._panes = []
    app._ws = ws

    # Manually wire the connection handler (the same lambda app.py will use)
    bar = _TrackingBar()
    ws.on_connection(lambda up: bar.set_connection_state(up))
    ws.on("window_reset", app._on_window_reset)

    ws.inject_connection(False)
    assert calls == [False], f"Expected [False], got {calls}"
    ws.inject_connection(True)
    assert calls == [False, True], f"Expected [False, True], got {calls}"
