from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec, Place


class _StubSession:
    supports_idle_events = False

    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True

    async def send(self, text):
        pass

    async def events(self):
        return
        yield

    async def close(self):
        pass


def _manager(tmp_path):
    seen: list[Place | None] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None):
        seen.append(place)
        return _StubSession()

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus"),
                "vpsy": Agent(harness="claude-code", model="opus",
                              host="vps")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net",
                               cwd="/home/apiad/Workspace")},
        local_root=str(tmp_path))
    return mgr, seen


def test_default_spawn_is_local(tmp_path):
    mgr, seen = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main"))
    assert mgr.get(h).place == Place("local", str(tmp_path))
    # A default-local place tells the factory nothing it does not already
    # assume, so it is not passed — which is what keeps the pre-hosts
    # (profile, url, handle) factory signature working.
    assert seen[-1] is None


def test_a_local_cwd_override_does_reach_the_factory(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", cwd="/somewhere/else"))
    assert seen[-1] == Place("local", "/somewhere/else")


def test_explicit_host_reaches_the_factory(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps"))
    assert seen[-1] == Place("vps", "/home/apiad/Workspace")


def test_explicit_cwd_overrides_the_host_default(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps", cwd="/other"))
    assert seen[-1] == Place("vps", "/other")


def test_profile_host_default_applies(tmp_path):
    mgr, seen = _manager(tmp_path)
    asyncio.run(mgr.spawn("vpsy"))
    assert seen[-1] == Place("vps", "/home/apiad/Workspace")


def test_session_carries_its_place(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    assert mgr.get(h).place == Place("vps", "/home/apiad/Workspace")


def test_local_session_place_is_local(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main"))
    assert mgr.get(h).place.is_local


def test_unknown_host_raises_before_a_session_is_created(tmp_path):
    from aegis.hosts.errors import HostError
    mgr, _ = _manager(tmp_path)
    before = len(mgr.list_sessions())
    with pytest.raises(HostError, match="nowhere"):
        asyncio.run(mgr.spawn("main", host="nowhere"))
    assert len(mgr.list_sessions()) == before
