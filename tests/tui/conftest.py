"""Fixtures for tests/tui — shared across ws_client and remote_manager tests."""
from __future__ import annotations

import pytest


class FakeWsClient:
    """Minimal fake that satisfies the WsClient public surface used by RemoteSessionManager.

    Supports:
    - rpc_result(method, result) — pre-register results for rpc() calls (FIFO per method)
    - rpc_calls — list of (method, params) tuples in call order
    - inject_stream(kind, frame) — synchronously dispatch a stream frame to registered handlers
    - inject_session_list_stream(added, removed, updated) — convenience wrapper
    - subscribed_globals — set of global stream names subscribed via subscribe_global()
    - subscribed_sessions — list of (handle, tail) tuples from subscribe_session()
    - constants — dict (empty by default)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self._rpc_results: dict[str, list] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.subscribed_globals: set[str] = set()
        self.subscribed_sessions: list[tuple[str, int | None]] = []
        self._constants: dict = {}

    @property
    def constants(self) -> dict:
        return dict(self._constants)

    def rpc_result(self, method: str, result: dict) -> None:
        """Pre-register a result to return when rpc(method, ...) is called."""
        self._rpc_results.setdefault(method, []).append(result)

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        """Return the next pre-registered result for method, recording the call."""
        self.rpc_calls.append((method, params or {}))
        queue = self._rpc_results.get(method, [])
        if queue:
            return queue.pop(0)
        return {}

    def on(self, kind: str, fn) -> None:
        """Register a stream handler (mirrors WsClient.on)."""
        self._handlers.setdefault(kind, []).append(fn)

    async def subscribe_global(self, stream: str) -> None:
        self.subscribed_globals.add(stream)

    async def subscribe_session(self, handle: str, *, tail: int | None = None) -> None:
        self.subscribed_sessions.append((handle, tail))

    def inject_stream(self, kind: str, frame: dict) -> None:
        """Synchronously dispatch a stream frame to all registered handlers for kind."""
        for fn in list(self._handlers.get(kind, [])):
            fn(frame)

    def inject_session_list_stream(
        self,
        *,
        added: list | None = None,
        removed: list | None = None,
        updated: list | None = None,
    ) -> None:
        """Convenience: queue a list_sessions RPC result and/or dispatch a
        session_list stream frame.

        When called before start(), the added sessions are queued as the
        result for rpc("list_sessions") so start() picks them up.
        When called after start() (handlers already registered), the frame
        is also dispatched synchronously.
        """
        sessions = added or []
        # Queue the list_sessions RPC result so start() can pre-populate.
        self._rpc_results.setdefault("list_sessions", []).append(
            {"sessions": sessions})
        # Also dispatch synchronously in case handlers are already registered.
        self.inject_stream("session_list", {
            "added": sessions,
            "removed": removed or [],
            "updated": updated or [],
        })


@pytest.fixture
def fake_ws_client() -> FakeWsClient:
    """Return a fresh FakeWsClient for each test."""
    return FakeWsClient()
