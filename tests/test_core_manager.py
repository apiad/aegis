from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.mcp.bridge import AppBridge


class FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def make_mgr():
    agents = {"default": object(), "researcher": object()}
    return SessionManager(
        agents, "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None,
    )


def test_implements_appbridge():
    assert isinstance(make_mgr(), AppBridge)


@pytest.mark.asyncio
async def test_spawn_list_close():
    m = make_mgr()
    s = m._sync_spawn("default")
    assert s.handle in [si.handle for si in m.list_sessions()]
    assert sorted(m.list_agents()) == ["default", "researcher"]
    await m.close(s.handle)
    assert m.list_sessions() == []


@pytest.mark.asyncio
async def test_handoff_rejects_self_and_unknown():
    m = make_mgr()
    a = m._sync_spawn("default")
    assert "cannot hand off to yourself" in await m.handoff(
        a.handle, a.handle, "x")
    assert "no session" in await m.handoff(a.handle, "nope", "x")


@pytest.mark.asyncio
async def test_spawn_unknown_slug_raises():
    m = make_mgr()
    with pytest.raises(KeyError):
        m._sync_spawn("nope")


@pytest.mark.asyncio
async def test_mru_active_after_spawn():
    m = make_mgr()
    m._sync_spawn("default")
    b = m._sync_spawn("researcher")
    info = m.list_sessions()
    actives = [si for si in info if si.active]
    assert len(actives) == 1
    assert actives[0].handle == b.handle


@pytest.mark.asyncio
async def test_close_all_clears_sessions():
    m = make_mgr()
    m._sync_spawn("default")
    m._sync_spawn("researcher")
    await m.close_all()
    assert m.list_sessions() == []


@pytest.mark.asyncio
async def test_spawn_with_opening_prompt_kicks_first_turn():
    sent: list[str] = []

    class Recording:
        def __init__(self): self.started = self.closed = False
        async def start(self): self.started = True
        async def send(self, t): sent.append(t)
        async def close(self): self.closed = True

        async def events(self):
            from aegis.events import Result
            await asyncio.sleep(0)
            yield Result(duration_ms=1, is_error=False, usage=None)

    agents = {"default": object()}
    m = SessionManager(agents, "default",
                       make_session=lambda a, u, h: Recording(),
                       mcp=None)
    s = m._sync_spawn("default", opening_prompt="hello there")
    # spawn wraps the first send() in asyncio.create_task; yield once so
    # that outer task runs and sets s._task.
    await asyncio.sleep(0)
    assert s._task is not None
    await s._task
    assert sent == ["hello there"]


@pytest.mark.asyncio
async def test_spawn_with_explicit_handle():
    m = make_mgr()
    s = m._sync_spawn("default", handle="vivid-laplace")
    assert s.handle == "vivid-laplace"


@pytest.mark.asyncio
async def test_sessionmanager_async_spawn_returns_handle():
    m = make_mgr()
    handle = await m.spawn("default", handle="vivid-laplace")
    assert handle == "vivid-laplace"
    assert any(s.handle == "vivid-laplace" for s in m._sessions)


@pytest.mark.asyncio
async def test_sync_spawn_still_works_for_queue():
    m = make_mgr()
    s = m._sync_spawn("default", handle="w1")
    assert s.handle == "w1"


@pytest.mark.asyncio
async def test_spawn_threads_inbox_router_when_set():
    from aegis.queue import InboxRouter
    inbox = InboxRouter()
    m = SessionManager({"default": object()}, "default",
                       make_session=lambda a, u, h: FakeHarness(),
                       mcp=None, inbox=inbox)
    s = m._sync_spawn("default")
    assert s._inbox is inbox
