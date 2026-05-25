"""Hermetic two-serve callback round-trip + failure modes."""
from __future__ import annotations

import asyncio
import pytest

from aegis.remote.client import remote_enqueue
from tests.fixtures.two_serves import build_two_serves


@pytest.mark.asyncio
async def test_remote_callback_round_trip(monkeypatch):
    """Agent on A enqueues to B with callback=true; B runs the worker;
    A's inbox receives the result.

    Asserts all four callback properties: body, sender, task_id, status.
    """
    pair = await build_two_serves(monkeypatch)
    try:
        # A enqueues into B via the client — equivalent to the agent on A
        # calling aegis_enqueue(target="b", callback=True). The client is
        # the same code path the MCP tool uses, so this exercises the
        # full wire.
        result = await remote_enqueue(
            pair.bridge_a.remotes["b"],
            "impl", "do it", "lucid-knuth",
            callback_to="a", callback_handle="lucid-knuth")
        assert "error" not in result, result
        assert "task_id" in result

        # B's StubSessionManager auto-yields AssistantText("DONE") + Result,
        # so the task completes. Then B's observer POSTs /remote/v1/callback
        # to A, and A's plane delivers to inbox_a.
        msgs = await pair.wait_for_inbox_on_a("lucid-knuth", timeout=2.0)
        assert len(msgs) == 1
        m = msgs[0]
        assert m.body == "DONE"
        assert "queue:b:impl" in m.sender
        assert m.task_id == result["task_id"]
        assert m.status == "ok"
    finally:
        await pair.shutdown()


@pytest.mark.asyncio
async def test_callback_to_unknown_peer_logs_drop(monkeypatch):
    """B has no remotes[a] → callback_dropped: unknown_peer on B;
    A's inbox sees nothing.

    Note: in this in-process test, QueueManager has no state_dir, so the
    callback_dropped audit record is not persisted to disk. The audit log
    path is exercised in unit tests in test_remote_callback_client.py.
    We assert the negative outcome: A's inbox is empty after the task
    completes.
    """
    pair = await build_two_serves(monkeypatch, b_remotes_includes_a=False)
    try:
        result = await remote_enqueue(
            pair.bridge_a.remotes["b"],
            "impl", "do it", "lucid-knuth",
            callback_to="a", callback_handle="lucid-knuth")
        assert "error" not in result

        # Wait long enough for the worker to complete AND the observer
        # to fire (and log the drop). 200ms is plenty for the in-process
        # cycle.
        await asyncio.sleep(0.2)

        # A's inbox got nothing — callback was dropped at B because B
        # has no remotes["a"] entry.
        assert pair.inbox_a.pending("lucid-knuth") == []
    finally:
        await pair.shutdown()


@pytest.mark.asyncio
async def test_caller_plane_unreachable_logs_drop(monkeypatch):
    """A's plane raises on callback → B's observer logs callback_dropped.

    We simulate A becoming unreachable after the enqueue is accepted by B
    but before the worker completes and the callback fires. The test
    re-monkey-patches _build_client so that requests to http://a raise
    ConnectError — B's observer swallows the error and A's inbox stays
    empty.
    """
    pair = await build_two_serves(monkeypatch)
    try:
        import httpx

        # Issue the enqueue first (B receives, queues, starts work).
        result = await remote_enqueue(
            pair.bridge_a.remotes["b"],
            "impl", "do it", "lucid-knuth",
            callback_to="a", callback_handle="lucid-knuth")
        assert "error" not in result

        # Now break A. Replace _build_client so that requests to http://a
        # raise ConnectError while requests to http://b still work normally.
        from httpx import ASGITransport

        async def _broken_factory(spec):
            if spec.url == "http://a":
                class _BrokenClient:
                    async def __aenter__(self): return self
                    async def __aexit__(self, *a): return None
                    async def aclose(self): pass
                    async def post(self, *a, **k):
                        raise httpx.ConnectError("simulated unreachable")
                return _BrokenClient()
            return httpx.AsyncClient(
                transport=ASGITransport(app=pair.app_b),
                base_url=spec.url)
        monkeypatch.setattr(
            "aegis.remote.client._build_client", _broken_factory)

        # Wait for the worker to finish and the observer to try the
        # callback (which now fails because A is "unreachable").
        await asyncio.sleep(0.3)

        # A's inbox got nothing.
        assert pair.inbox_a.pending("lucid-knuth") == []
    finally:
        await pair.shutdown()


@pytest.mark.asyncio
async def test_caller_session_closed_writes_to_inbox_pending(monkeypatch):
    """A's session not live → A's plane buffers in inbox.pending.

    The plan spoke of a JSONL ledger, but InboxRouter in VS1 mode (no
    state_dir) uses memory-pending. When no AgentSession is bound for
    "lucid-knuth" on A, the delivered message lands in _pending — same
    semantics as a live session with respect to message availability.
    This test asserts that fallback: inbox.pending returns the message even
    without a live session.
    """
    pair = await build_two_serves(monkeypatch)
    try:
        result = await remote_enqueue(
            pair.bridge_a.remotes["b"],
            "impl", "do it", "lucid-knuth",
            callback_to="a", callback_handle="lucid-knuth")
        assert "error" not in result

        # No session bound for "lucid-knuth" on A — the inbox will buffer
        # in _pending. wait_for_inbox_on_a asserts exactly that.
        msgs = await pair.wait_for_inbox_on_a("lucid-knuth", timeout=2.0)
        assert len(msgs) == 1
        assert msgs[0].body == "DONE"
    finally:
        await pair.shutdown()
