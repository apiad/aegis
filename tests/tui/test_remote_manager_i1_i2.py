"""Tests for I1 (list_agents returns []) and I2 (tunnel not torn down).

I1 — after start(), list_agents() must return the result of the
     rpc("list_agents") wire call, not the initial empty list.

I2 — RemoteSessionManager.close() must:
     - call await self._ws.close()
     - call await self._tunnel.__aexit__(None, None, None) if tunnel exists
     - be idempotent (safe to call twice)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeTunnel:
    def __init__(self) -> None:
        self.exit_calls: list = []

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exit_calls.append((exc_type, exc_val, exc_tb))


class _FakeWsWithClose:
    """FakeWsClient that also tracks close() calls."""

    def __init__(self) -> None:
        self._handlers: dict = {}
        self._rpc_results: dict = {}
        self.rpc_calls: list = []
        self.subscribed_globals: set = set()
        self.subscribed_sessions: list = []
        self.close_called = False

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

    async def subscribe_global(self, stream: str) -> None:
        self.subscribed_globals.add(stream)

    async def subscribe_session(self, handle: str, *, tail=None) -> None:
        self.subscribed_sessions.append((handle, tail))

    def inject_stream(self, kind: str, frame: dict) -> None:
        for fn in list(self._handlers.get(kind, [])):
            fn(frame)

    def inject_session_list_stream(self, *, added=None, removed=None,
                                    updated=None) -> None:
        sessions = added or []
        self._rpc_results.setdefault("list_sessions", []).append(
            {"sessions": sessions})
        self.inject_stream("session_list", {
            "added": sessions,
            "removed": removed or [],
            "updated": updated or [],
        })

    async def close(self) -> None:
        self.close_called = True


# ---------------------------------------------------------------------------
# I1 tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_agents_returns_empty_before_start():
    """Sanity: before start(), list_agents() is empty (no wire call yet)."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    mgr = RemoteSessionManager(ws)
    assert mgr.list_agents() == []


@pytest.mark.asyncio
async def test_start_fetches_agent_list_from_wire():
    """After start(), list_agents() must return agents from rpc('list_agents')."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    ws.rpc_result("list_agents", {"agents": ["alpha", "beta"]})
    mgr = RemoteSessionManager(ws)
    await mgr.start()
    result = mgr.list_agents()
    assert set(result) == {"alpha", "beta"}, (
        f"Expected ['alpha', 'beta'] but got {result}"
    )


@pytest.mark.asyncio
async def test_start_calls_list_agents_rpc():
    """start() must issue rpc('list_agents', ...) to populate the agent cache."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    ws.rpc_result("list_agents", {"agents": ["main"]})
    mgr = RemoteSessionManager(ws)
    await mgr.start()
    methods = [call[0] for call in ws.rpc_calls]
    assert "list_agents" in methods, (
        f"start() did not call rpc('list_agents'). Calls: {methods}"
    )


# ---------------------------------------------------------------------------
# I2 tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_calls_ws_close():
    """RemoteSessionManager.shutdown() must call ws.close()."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    mgr = RemoteSessionManager(ws)
    await mgr.shutdown()
    assert ws.close_called, "ws.close() was not called by mgr.shutdown()"


@pytest.mark.asyncio
async def test_shutdown_calls_tunnel_aexit_when_tunnel_present():
    """When _tunnel is set, shutdown() must call tunnel.__aexit__(None,None,None)."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    mgr = RemoteSessionManager(ws)
    tunnel = _FakeTunnel()
    mgr._tunnel = tunnel
    await mgr.shutdown()
    assert len(tunnel.exit_calls) == 1, (
        f"Expected 1 tunnel.__aexit__ call, got {len(tunnel.exit_calls)}"
    )
    assert tunnel.exit_calls[0] == (None, None, None)


@pytest.mark.asyncio
async def test_shutdown_idempotent():
    """Calling shutdown() twice must not raise or double-call tunnel.__aexit__."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    mgr = RemoteSessionManager(ws)
    tunnel = _FakeTunnel()
    mgr._tunnel = tunnel

    # First shutdown
    await mgr.shutdown()
    # Second shutdown — must not raise
    try:
        await mgr.shutdown()
    except Exception as exc:
        pytest.fail(f"second shutdown() raised: {exc}")

    # Tunnel should have been exited only once (idempotent)
    assert len(tunnel.exit_calls) <= 1, (
        f"tunnel.__aexit__ called {len(tunnel.exit_calls)} times; expected ≤1"
    )


@pytest.mark.asyncio
async def test_shutdown_no_tunnel_does_not_raise():
    """shutdown() without a tunnel must not raise AttributeError."""
    from aegis.tui.remote_manager import RemoteSessionManager
    ws = _FakeWsWithClose()
    mgr = RemoteSessionManager(ws)
    # No _tunnel attribute set
    try:
        await mgr.shutdown()
    except AttributeError as exc:
        pytest.fail(f"shutdown() raised AttributeError with no tunnel: {exc}")
