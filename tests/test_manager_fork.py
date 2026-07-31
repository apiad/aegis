from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.tui.state import AgentState


class FakeSession:
    """Records the fork_from it was constructed with, so tests can assert
    what the manager snapshotted rather than what it promised to."""

    def __init__(self, events=(), *, session_id=None, fork_from=None):
        self._events = list(events)
        self.session_id = session_id
        self.fork_from = fork_from

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


class FakeAgent:
    harness = "claude-code"
    model = "opus"
    effort = "high"
    permission = "full"
    prompt = None


def _mgr(built: list, *, supports_fork=True):
    def make_session(profile, url, handle, fork_from=None):
        s = FakeSession(session_id="parent-sid" if fork_from is None else None,
                        fork_from=fork_from)
        built.append((handle, fork_from))
        return s

    mgr = SessionManager(agents={"default": FakeAgent()},
                         default_agent="default",
                         make_session=make_session, mcp=None)
    mgr._fork_capability = lambda harness: supports_fork
    return mgr


@pytest.mark.asyncio
async def test_fork_builds_child_from_parents_session_id():
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    child = await mgr.fork(parent, prompt="diverge here")
    assert child != parent
    assert built[-1][1] == "parent-sid"


@pytest.mark.asyncio
async def test_fork_records_provenance_on_the_child():
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    child = await mgr.fork(parent, prompt="go")
    prov = mgr.get(child).forked_from
    assert prov["handle"] == parent
    assert prov["session_id"] == "parent-sid"
    assert prov["log_id"] == mgr.get(parent).log_id


@pytest.mark.asyncio
async def test_fork_gives_the_child_its_own_log_id():
    """A fork is a new conversation that shares a prefix. Two
    conversations sharing one log file is the bug that buried 160."""
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    child = await mgr.fork(parent, prompt="go")
    assert mgr.get(child).log_id != mgr.get(parent).log_id


@pytest.mark.asyncio
async def test_fork_leaves_the_parents_session_id_unmoved():
    """The invariant a fork could plausibly break, and it would break
    silently: the parent must be untouched by being forked."""
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    before = mgr.get(parent).session_id
    await mgr.fork(parent, prompt="go")
    assert mgr.get(parent).session_id == before


@pytest.mark.asyncio
async def test_fork_does_not_write_to_the_parents_log(tmp_path):
    built: list = []
    mgr = _mgr(built)
    mgr.attach_persistence(tmp_path)
    parent = await mgr.spawn("default")
    await mgr.get(parent).send("go")
    await mgr.get(parent)._task
    from aegis.state.session_log import session_log_path
    p = session_log_path(tmp_path, mgr.get(parent).log_id)
    before = p.read_bytes() if p.exists() else b""
    await mgr.fork(parent, prompt="go")
    after = p.read_bytes() if p.exists() else b""
    assert after == before


@pytest.mark.asyncio
async def test_fork_refuses_unknown_handle():
    mgr = _mgr([])
    with pytest.raises(ValueError, match="no session 'ghost'"):
        await mgr.fork("ghost", prompt="go")


@pytest.mark.asyncio
async def test_fork_refuses_mid_turn_parent():
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    mgr.get(parent).state = AgentState.working
    with pytest.raises(ValueError, match="mid-turn"):
        await mgr.fork(parent, prompt="go")


@pytest.mark.asyncio
async def test_fork_refuses_when_driver_cannot_fork():
    built: list = []
    mgr = _mgr(built, supports_fork=False)
    parent = await mgr.spawn("default")
    with pytest.raises(ValueError, match="does not support session fork"):
        await mgr.fork(parent, prompt="go")


@pytest.mark.asyncio
async def test_fork_refuses_parent_with_no_session_id():
    def make_session(profile, url, handle, fork_from=None):
        return FakeSession(session_id=None, fork_from=fork_from)

    mgr = SessionManager(agents={"default": FakeAgent()},
                         default_agent="default",
                         make_session=make_session, mcp=None)
    mgr._fork_capability = lambda harness: True
    parent = await mgr.spawn("default")
    with pytest.raises(ValueError, match="no session id yet"):
        await mgr.fork(parent, prompt="go")


@pytest.mark.asyncio
async def test_fork_without_prompt_sends_nothing():
    """A bare /fork inherits the conversation and waits. Sending an
    empty opening turn would burn a turn to say nothing."""
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    child = await mgr.fork(parent)
    assert mgr.get(child)._task is None


@pytest.mark.asyncio
async def test_fork_honours_a_requested_slug():
    built: list = []
    mgr = _mgr(built)
    parent = await mgr.spawn("default")
    child = await mgr.fork(parent, slug="branch-a")
    assert child == "branch-a"
