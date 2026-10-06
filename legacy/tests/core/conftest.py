"""A SessionManager holding one local session, for the recovery plane.

`reconnect(allow_local=True)` and `recovery.rebuild` are two tests files
over the same rig: a manager whose factory hands out a stub harness with
(or deliberately without) a conversation id. Shared here rather than
imported across test modules.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec


class StubHarness:
    """Same shape as the double the pre-existing reconnect tests use."""

    supports_idle_events = False

    def __init__(self, session_id=None):
        self._session_id = session_id
        self.closed = False
        self.sent: list[str] = []

    @property
    def session_id(self):
        return self._session_id

    async def start(self):
        pass

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        return
        yield

    async def close(self):
        self.closed = True


def _manager(tmp_path, session_id):
    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        return StubHarness(session_id)

    return SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        roots=AegisRoots.for_project(tmp_path))


async def _spawned(tmp_path, session_id):
    mgr = _manager(tmp_path, session_id)
    handle = await mgr.spawn("main")
    s = mgr.get(handle)
    assert s.place.is_local
    # What `deliver` was handed, recorded off the inbox observer so a test
    # reads it synchronously. The turn `deliver` starts is a task, so
    # asserting on the harness's `sent` would race it; the observer fires
    # inside `deliver` itself. `adopt` keeps observers, which is the whole
    # reason a rebuild can be asserted on at all.
    s.delivered_bodies: list[str] = []
    s.add_inbox_observer(lambda sess, msg: sess.delivered_bodies.append(msg.body))
    return mgr, handle


@pytest.fixture
async def brain_with_local_session(tmp_path):
    return await _spawned(tmp_path, "sid-1")


@pytest.fixture
async def brain_with_local_session_no_id(tmp_path):
    return await _spawned(tmp_path, None)
