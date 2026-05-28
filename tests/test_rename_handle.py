from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aegis.core.manager import SessionManager
from aegis.queue import InboxMessage, now_iso, sender_agent
from aegis.queue.inbox import InboxRouter


class FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr(*, inbox=None) -> SessionManager:
    agents = {"default": object()}
    return SessionManager(
        agents, "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None,
        inbox=inbox,
    )


@pytest.mark.asyncio
async def test_rename_handle_swaps_session_handle():
    m = _mgr()
    s = m._sync_spawn("default")
    old = s.handle
    res = await m.rename_handle(old, "lucid-river")
    assert res == {"ok": True, "old": old, "new": "lucid-river"}
    assert s.handle == "lucid-river"
    assert [si.handle for si in m.list_sessions()] == ["lucid-river"]


@pytest.mark.asyncio
async def test_rename_handle_updates_mru():
    m = _mgr()
    a = m._sync_spawn("default")
    b = m._sync_spawn("default")
    assert m._mru[0] == b.handle
    await m.rename_handle(b.handle, "lucid-river")
    assert m._mru[0] == "lucid-river"
    assert a.handle in m._mru


@pytest.mark.asyncio
async def test_rename_handle_unknown_old_rejected():
    m = _mgr()
    res = await m.rename_handle("nope-nobody", "lucid-river")
    assert "error" in res
    assert "no session" in res["error"]


@pytest.mark.asyncio
async def test_rename_handle_collision_rejected():
    m = _mgr()
    a = m._sync_spawn("default")
    b = m._sync_spawn("default")
    res = await m.rename_handle(a.handle, b.handle)
    assert "error" in res
    assert "already in use" in res["error"]


@pytest.mark.asyncio
async def test_rename_handle_idempotent_when_old_equals_new():
    m = _mgr()
    s = m._sync_spawn("default")
    res = await m.rename_handle(s.handle, s.handle)
    assert res == {"ok": True, "old": s.handle, "new": s.handle}


@pytest.mark.parametrize("bad", [
    "single",                      # one word
    "four-words-too-many-here",    # four words
    "UPPER-case",                  # uppercase
    "has space",                   # space
    "trailing-",                   # dangling hyphen
    "-leading",                    # leading hyphen
    "double--hyphen",              # empty segment
    "",                            # empty
    "a/b-c",                       # slash
])
@pytest.mark.asyncio
async def test_rename_handle_format_rejected(bad: str):
    m = _mgr()
    s = m._sync_spawn("default")
    res = await m.rename_handle(s.handle, bad)
    assert "error" in res
    assert "format" in res["error"]


@pytest.mark.parametrize("good", [
    "lucid-river",                 # two words
    "lucid-river-runs",            # three words
    "agent-7",                     # digit segment
    "n1-n2",                       # mixed alnum
])
@pytest.mark.asyncio
async def test_rename_handle_format_accepted(good: str):
    m = _mgr()
    s = m._sync_spawn("default")
    res = await m.rename_handle(s.handle, good)
    assert res["ok"] is True
    assert s.handle == good


@pytest.mark.asyncio
async def test_rename_handle_migrates_inbox_binding():
    inbox = InboxRouter()
    m = _mgr(inbox=inbox)
    s = m._sync_spawn("default")
    old = s.handle
    await m.rename_handle(old, "lucid-river")
    # old binding gone, new binding present
    assert old not in inbox._sessions
    assert inbox._sessions["lucid-river"] is s


@pytest.mark.asyncio
async def test_rename_handle_migrates_pending_messages():
    inbox = InboxRouter()
    # Queue a message before the session is bound — lands in pending
    msg = InboxMessage(
        sender=sender_agent("ghost-handle"),
        timestamp=now_iso(),
        body="held in pending")
    await inbox.deliver("buffer-handle", msg)
    assert inbox.pending("buffer-handle") == [msg]

    # Manually rename pending bucket (no live session involved)
    inbox.rename("buffer-handle", "fresh-handle")
    assert inbox.pending("buffer-handle") == []
    assert inbox.pending("fresh-handle") == [msg]


@pytest.mark.asyncio
async def test_inbox_rename_noop_when_old_equals_new():
    inbox = InboxRouter()
    msg = InboxMessage(
        sender=sender_agent("x"), timestamp=now_iso(), body="b")
    await inbox.deliver("h", msg)
    inbox.rename("h", "h")
    assert inbox.pending("h") == [msg]


# --- MCP tool surface -----------------------------------------------------


class _FakeBridge:
    """Minimal AppBridge stub exposing only what aegis_rename calls."""

    def __init__(self, sm: SessionManager) -> None:
        self._sm = sm
        # surfaces the server build path touches at construction time
        self.queue_manager = None
        self.inbox_router = None
        self.canvas_manager = None
        self.terminal_manager = None
        self.groups = MagicMock()
        self.remotes = {}
        self.scheduler = None
        self.state_root = None
        self.workflow_registry = None

    def inline_schedule_names(self): return set()
    def list_sessions(self): return self._sm.list_sessions()
    def list_agents(self): return self._sm.list_agents()
    async def handoff(self, *a, **k): return ""
    async def spawn(self, *a, **k): return ""
    async def close(self, *a, **k): return None

    async def rename_handle(self, old: str, new: str) -> dict:
        return await self._sm.rename_handle(old, new)


@pytest.mark.asyncio
async def test_mcp_aegis_rename_delegates_to_bridge():
    from aegis.mcp.server import _aegis_rename_impl
    m = _mgr()
    s = m._sync_spawn("default")
    old = s.handle
    bridge = _FakeBridge(m)
    res = await _aegis_rename_impl(
        bridge, old_handle=old, new_handle="lucid-river")
    assert res == {"ok": True, "old": old, "new": "lucid-river"}
    assert s.handle == "lucid-river"


@pytest.mark.asyncio
async def test_mcp_aegis_rename_returns_error_on_collision():
    from aegis.mcp.server import _aegis_rename_impl
    m = _mgr()
    a = m._sync_spawn("default")
    b = m._sync_spawn("default")
    bridge = _FakeBridge(m)
    res = await _aegis_rename_impl(
        bridge, old_handle=a.handle, new_handle=b.handle)
    assert "error" in res
