"""Tests for RemoteSessionManager — conversation-loop AppBridge over WS.

These tests use a FakeWsClient that records calls and injects responses
without actual network connections.
"""
from __future__ import annotations

import pytest

from aegis.tui.remote_manager import RemoteSessionManager, RemoteUnsupportedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeInboxMessage:
    """Minimal stand-in for an inbox message with a .body attribute."""
    def __init__(self, body: str) -> None:
        self.body = body


def _fake_inbox_message(text: str) -> _FakeInboxMessage:
    return _FakeInboxMessage(text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_and_close_round_trip_via_rpc(fake_ws_client):
    fake_ws_client.rpc_result("spawn_session", {"handle": "quiet-turing"})
    fake_ws_client.rpc_result("close_session", {"ok": True})
    mgr = RemoteSessionManager(fake_ws_client)
    handle = await mgr.spawn("main")
    assert handle == "quiet-turing"
    assert fake_ws_client.rpc_calls[0] == ("spawn_session",
                                            {"agent_profile": "main"})
    await mgr.close("quiet-turing")
    assert fake_ws_client.rpc_calls[1] == ("close_session",
                                            {"handle": "quiet-turing"})


@pytest.mark.asyncio
async def test_deliver_returns_delivery_dataclass(fake_ws_client):
    fake_ws_client.rpc_result("deliver", {"delivery": "landed", "depth": 0})
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "h", "agent_slug": "main", "state": "ready",
                "active": True, "unseen": False}])
    await mgr.start()
    session = mgr.get("h")
    receipt = await session.deliver(_fake_inbox_message("hi"))
    assert receipt.disposition == "landed"
    assert receipt.depth == 0


@pytest.mark.asyncio
async def test_event_stream_routes_to_registered_observer(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "h", "agent_slug": "main", "state": "ready",
                "active": True, "unseen": False}])
    await mgr.start()
    session = mgr.get("h")
    got: list = []
    session.add_event_observer(got.append)
    fake_ws_client.inject_stream("event", {
        "handle": "h", "seq": 42, "event_type": "AssistantText",
        "event": {"type": "AssistantText", "text": "hi", "message_id": None,
                  "t": "AssistantText"}})
    assert len(got) == 1
    assert got[0].__class__.__name__ == "AssistantText"


def test_disabled_plane_raises_on_access(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    with pytest.raises(RemoteUnsupportedError, match="not available in --remote v1"):
        mgr.canvas_manager.open("x")
    with pytest.raises(RemoteUnsupportedError):
        mgr.queue_manager.enqueue("q", "payload")


@pytest.mark.asyncio
async def test_start_subscribes_to_session_list(fake_ws_client):
    """start() should call subscribe_global('session_list')."""
    mgr = RemoteSessionManager(fake_ws_client)
    await mgr.start()
    assert "session_list" in fake_ws_client.subscribed_globals


@pytest.mark.asyncio
async def test_session_list_stream_populates_sessions(fake_ws_client):
    """A session_list stream frame with 'added' should create RemoteAgentSession entries."""
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "swift-bohr", "agent_slug": "main",
                "state": "ready", "active": True, "unseen": False}])
    await mgr.start()
    assert mgr.get("swift-bohr") is not None
    infos = mgr.list_sessions()
    assert any(s.handle == "swift-bohr" for s in infos)


@pytest.mark.asyncio
async def test_session_list_removed_clears_session(fake_ws_client):
    """A session_list stream frame with 'removed' should drop the session."""
    mgr = RemoteSessionManager(fake_ws_client)
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "gone-session", "agent_slug": "main",
                "state": "ready", "active": True, "unseen": False}])
    await mgr.start()
    assert mgr.get("gone-session") is not None
    fake_ws_client.inject_stream("session_list", {"removed": ["gone-session"], "added": [], "updated": []})
    assert mgr.get("gone-session") is None


@pytest.mark.asyncio
async def test_interrupt_sends_rpc(fake_ws_client):
    fake_ws_client.rpc_result("interrupt_session", {"ok": True})
    mgr = RemoteSessionManager(fake_ws_client)
    await mgr.interrupt("some-handle")
    assert any(c[0] == "interrupt_session" for c in fake_ws_client.rpc_calls)


@pytest.mark.asyncio
async def test_rename_handle_sends_rpc(fake_ws_client):
    fake_ws_client.rpc_result("rename_handle", {"old": "a", "new": "b"})
    mgr = RemoteSessionManager(fake_ws_client)
    result = await mgr.rename_handle("a", "b")
    assert result == {"old": "a", "new": "b"}


def test_inline_schedule_names_returns_empty_set(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    assert mgr.inline_schedule_names() == set()


def test_remotes_is_empty_dict(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    assert mgr.remotes == {}


def test_scheduler_is_none(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    assert mgr.scheduler is None


def test_register_agent_raises_remote_unsupported(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    with pytest.raises(RemoteUnsupportedError):
        mgr.register_agent("slug", object())


def test_register_queue_raises_remote_unsupported(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    with pytest.raises(RemoteUnsupportedError):
        mgr.register_queue(object())


def test_reload_plugins_raises_remote_unsupported(fake_ws_client):
    mgr = RemoteSessionManager(fake_ws_client)
    with pytest.raises(RemoteUnsupportedError):
        mgr.reload_plugins()
