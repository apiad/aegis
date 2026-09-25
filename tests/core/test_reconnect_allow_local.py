"""reconnect refuses local sessions because the manual /reconnect command
is documented as a remote-link repair. The MECHANISM is provider-level
resume_from, which is how every boot resume works, locally included. The
recovery plane needs it for local workers, so the guard becomes a
parameter rather than a property of the code.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec


class _StubSession:
    """Same shape as the double the pre-existing reconnect tests use."""

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


def _manager(tmp_path, session_id):
    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        return _StubSession(session_id)

    return SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        roots=AegisRoots.for_project(tmp_path))


@pytest.fixture
async def brain_with_local_session(tmp_path):
    mgr = _manager(tmp_path, session_id="sid-1")
    handle = await mgr.spawn("main")
    assert mgr.get(handle).place.is_local
    return mgr, handle


@pytest.fixture
async def brain_with_local_session_no_id(tmp_path):
    mgr = _manager(tmp_path, session_id=None)
    handle = await mgr.spawn("main")
    assert mgr.get(handle).place.is_local
    return mgr, handle


async def test_local_session_is_refused_by_default(brain_with_local_session):
    mgr, handle = brain_with_local_session
    with pytest.raises(ValueError, match="reconnect is for remote sessions"):
        await mgr.reconnect(handle)


async def test_local_session_is_allowed_when_asked(brain_with_local_session):
    mgr, handle = brain_with_local_session
    result = await mgr.reconnect(handle, allow_local=True)
    assert handle in result


async def test_a_session_with_no_id_is_refused_even_with_allow_local(
    brain_with_local_session_no_id,
):
    """allow_local lifts one guard, not both. Resuming on no id builds a
    fresh conversation and calls it a recovery."""
    mgr, handle = brain_with_local_session_no_id
    with pytest.raises(ValueError, match="no session id"):
        await mgr.reconnect(handle, allow_local=True)
