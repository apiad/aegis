from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec, Place


class _StubSession:
    supports_idle_events = False

    def __init__(self, session_id=None):
        self._session_id = session_id
        self.closed = False

    @property
    def session_id(self):
        return self._session_id

    async def start(self):
        pass

    async def send(self, text):
        pass

    async def events(self):
        return
        yield

    async def close(self):
        self.closed = True


def _manager(tmp_path, session_id="sid-1"):
    built: list[dict] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        built.append({"place": place, "resume_from": resume_from,
                      "handle": handle})
        return _StubSession(session_id)

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        local_root=str(tmp_path))
    return mgr, built


def test_reconnect_resumes_on_the_same_place(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.reconnect(h))
    assert built[-1]["place"] == Place("vps", "/w")
    assert built[-1]["resume_from"] == "sid-1"


def test_reconnect_keeps_the_same_handle_and_session(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    before = mgr.get(h)
    asyncio.run(mgr.reconnect(h))
    # Same AgentSession object: the handle, log id, inbox binding and
    # observers all survive. Only the process underneath is new.
    assert mgr.get(h) is before
    assert built[-1]["handle"] == h


def test_reconnect_closes_the_dead_harness(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    dead = mgr.get(h)._session
    asyncio.run(mgr.reconnect(h))
    assert dead.closed
    assert mgr.get(h)._session is not dead


def test_reconnect_refuses_without_a_session_id(tmp_path):
    mgr, _ = _manager(tmp_path, session_id=None)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    with pytest.raises(ValueError, match="session id"):
        asyncio.run(mgr.reconnect(h))


def test_reconnect_refuses_on_a_local_session(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main"))
    with pytest.raises(ValueError, match="local"):
        asyncio.run(mgr.reconnect(h))


def test_reconnect_lists_every_refusal_reason_at_once(tmp_path):
    mgr, _ = _manager(tmp_path, session_id=None)
    h = asyncio.run(mgr.spawn("main"))
    with pytest.raises(ValueError) as e:
        asyncio.run(mgr.reconnect(h))
    assert "local" in str(e.value) and "session id" in str(e.value)


# --- @host parsing --------------------------------------------------------


def test_plain_agent_has_no_host():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code") == ("claude-code", None, None)


def test_agent_at_host():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code@vps") == ("claude-code", "vps", None)


def test_agent_at_host_with_cwd():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code@vps:/srv/app") == (
        "claude-code", "vps", "/srv/app")


def test_cwd_may_contain_colons_after_the_first():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@vps:/srv/a:b") == ("main", "vps", "/srv/a:b")


def test_empty_host_is_treated_as_absent():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@") == ("main", None, None)


def test_empty_cwd_is_treated_as_absent():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@vps:") == ("main", "vps", None)


def test_spawn_command_passes_host_and_cwd(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps", cwd="/srv/app"))
    assert mgr.get(h).place == Place("vps", "/srv/app")


def test_session_info_reports_the_host(tmp_path):
    mgr, _ = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.spawn("main"))
    assert sorted(s.host for s in mgr.list_sessions()) == ["local", "vps"]
