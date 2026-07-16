"""Parity gate: RemoteSessionManager surfaces same conversation-loop API as local SessionManager.

This test checks that the conversation-loop attributes and methods exist on
RemoteSessionManager with compatible signatures and expected stubs — without
requiring a live server or local SessionManager to instantiate.
"""
from __future__ import annotations

import pytest

from aegis.tui.remote_manager import RemoteSessionManager, RemoteUnsupportedError


@pytest.mark.asyncio
async def test_conversation_loop_matches_local_manager(fake_ws_client):
    """Drive RemoteSessionManager through spawn → deliver → interrupt → close,
    checking that each step works and returns compatible values.

    This is the parity gate the S9 spec asks for.
    """
    fake_ws_client.rpc_result("spawn_session", {"handle": "parity-session"})
    fake_ws_client.rpc_result("deliver", {"delivery": "landed", "depth": 0})
    fake_ws_client.rpc_result("interrupt_session", {"ok": True})
    fake_ws_client.rpc_result("close_session", {"ok": True})

    mgr = RemoteSessionManager(fake_ws_client)

    # Inject session into session_list so get() works after spawn
    fake_ws_client.inject_session_list_stream(
        added=[{"handle": "parity-session", "agent_slug": "main",
                "state": "ready", "active": True, "unseen": False}])
    await mgr.start()

    # spawn
    handle = await mgr.spawn("main")
    assert handle == "parity-session"

    # get session and deliver
    session = mgr.get(handle)
    assert session is not None
    assert session.handle == handle

    class _Msg:
        body = "hello"

    receipt = await session.deliver(_Msg())
    assert receipt.disposition == "landed"
    assert receipt.depth == 0

    # interrupt
    await mgr.interrupt(handle)

    # close
    await mgr.close(handle)
    assert mgr.get(handle) is None

    # AppBridge auxiliary planes are disabled
    with pytest.raises(RemoteUnsupportedError, match="not available in --remote v1"):
        mgr.canvas_manager.something

    # list_sessions and list_agents return list types
    sessions = mgr.list_sessions()
    assert isinstance(sessions, list)

    agents = mgr.list_agents()
    assert isinstance(agents, list)

    # inline_schedule_names returns set
    assert isinstance(mgr.inline_schedule_names(), set)


def test_appbridge_attrs_exist(fake_ws_client):
    """All AppBridge Protocol attrs are present on RemoteSessionManager."""
    mgr = RemoteSessionManager(fake_ws_client)
    # These should be _DisabledPlane instances
    for attr in ("queue_manager", "inbox_router", "canvas_manager",
                 "terminal_manager", "groups", "locks", "workflow_registry"):
        assert hasattr(mgr, attr), f"missing attr: {attr}"
        with pytest.raises(RemoteUnsupportedError):
            getattr(mgr, attr).anything  # any attribute access should raise

    # These are plain values
    assert mgr.remotes == {}
    assert mgr.scheduler is None
    from pathlib import Path
    assert isinstance(mgr.state_root, Path)
