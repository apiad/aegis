from __future__ import annotations

import pytest

from aegis.mcp.identity import SessionTokens


class _FakeMCP:
    def __init__(self) -> None:
        self.url = "http://x/mcp/"
        self.tokens = SessionTokens()


class _FakeSession:
    handle = "fake"

    def add_event_observer(self, *a, **kw): return None
    def add_state_observer(self, *a, **kw): return None
    def add_inbox_observer(self, *a, **kw): return None
    def add_close_observer(self, *a, **kw): return None
    async def close(self): return None


def _agent():
    from aegis.config import Agent
    return Agent(harness="claude-code", model="opus", effort="medium",
                 permission="auto")


def _manager(factory, mcp):
    from aegis.core.manager import SessionManager
    return SessionManager({"main": _agent()}, "main", factory, mcp)


def test_a_spawned_handle_has_a_token():
    """The token must exist before the subprocess starts — it is baked
    into that subprocess's argv."""
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    assert mcp.tokens.resolve(token) == "alice"


def test_renaming_a_session_keeps_its_token_resolving():
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    mcp.tokens.rename("alice", "carol")
    assert mcp.tokens.resolve(token) == "carol"


def test_closing_a_session_revokes_its_token():
    mcp = _FakeMCP()
    token = mcp.tokens.mint("alice")
    mcp.tokens.revoke("alice")
    assert mcp.tokens.resolve(token) is None


@pytest.mark.asyncio
async def test_spawn_hands_the_factory_a_token_that_resolves_to_the_handle():
    """The end-to-end contract of this task: whatever handle the manager
    generates, the factory is handed a token that resolves back to it —
    and it is handed it at spawn, because the token is baked into the
    subprocess argv the factory is about to build."""
    seen: dict = {}

    def make_session(profile, mcp_url, handle, token="", **kw):
        seen["handle"] = handle
        seen["token"] = token
        return _FakeSession()

    mcp = _FakeMCP()
    mgr = _manager(make_session, mcp)
    await mgr.spawn("main")

    assert seen["token"], "the factory was handed no token"
    assert mcp.tokens.resolve(seen["token"]) == seen["handle"]


@pytest.mark.asyncio
async def test_closing_a_spawned_session_revokes_it_for_real():
    """Not the store in isolation — the manager's own close path."""
    mcp = _FakeMCP()
    seen: dict = {}

    def make_session(profile, mcp_url, handle, token="", **kw):
        seen["handle"], seen["token"] = handle, token
        return _FakeSession()

    mgr = _manager(make_session, mcp)
    await mgr.spawn("main")
    assert mcp.tokens.resolve(seen["token"]) == seen["handle"]

    await mgr.close(seen["handle"])
    assert mcp.tokens.resolve(seen["token"]) is None


@pytest.mark.asyncio
async def test_renaming_a_spawned_session_repoints_its_token_for_real():
    """A rename must carry identity across, or every call the session
    makes afterwards goes unattributed."""
    mcp = _FakeMCP()
    seen: dict = {}

    def make_session(profile, mcp_url, handle, token="", **kw):
        seen["handle"], seen["token"] = handle, token
        return _FakeSession()

    mgr = _manager(make_session, mcp)
    await mgr.spawn("main")

    out = await mgr.rename_handle(seen["handle"], "carol-the-renamed")
    assert out.get("ok"), out
    assert mcp.tokens.resolve(seen["token"]) == "carol-the-renamed"


@pytest.mark.asyncio
async def test_a_three_argument_factory_still_works():
    """SessionFactory is typed (object, str, str) and the manager says
    plain callables must keep working, so the token rides `extra` and is
    omitted when there is nothing to pass."""
    def legacy(profile, mcp_url, handle):
        return _FakeSession()

    mgr = _manager(legacy, None)
    await mgr.spawn("main")
