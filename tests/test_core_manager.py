from __future__ import annotations

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
    s = m.spawn("default")
    assert s.handle in [si.handle for si in m.list_sessions()]
    assert sorted(m.list_agents()) == ["default", "researcher"]
    await m.close(s.handle)
    assert m.list_sessions() == []


@pytest.mark.asyncio
async def test_handoff_rejects_self_and_unknown():
    m = make_mgr()
    a = m.spawn("default")
    assert "cannot hand off to yourself" in await m.handoff(
        a.handle, a.handle, "x")
    assert "no session" in await m.handoff(a.handle, "nope", "x")


@pytest.mark.asyncio
async def test_spawn_unknown_slug_raises():
    m = make_mgr()
    with pytest.raises(KeyError):
        m.spawn("nope")


@pytest.mark.asyncio
async def test_mru_active_after_spawn():
    m = make_mgr()
    a = m.spawn("default")
    b = m.spawn("researcher")
    info = m.list_sessions()
    actives = [si for si in info if si.active]
    assert len(actives) == 1
    assert actives[0].handle == b.handle


@pytest.mark.asyncio
async def test_close_all_clears_sessions():
    m = make_mgr()
    m.spawn("default"); m.spawn("researcher")
    await m.close_all()
    assert m.list_sessions() == []
