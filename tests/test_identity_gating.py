from __future__ import annotations

import pytest

from aegis.locks.bridge import make_locks_bridge
from aegis.mcp.identity import SessionTokens, verified_handle
from aegis.mcp.server import build_server
from tests.test_mcp_server import FakeBridge, _call


def _as(monkeypatch, tokens: SessionTokens, handle: str) -> None:
    """Pretend the in-flight request carries `handle`'s token.

    The in-memory FastMCP transport cannot carry headers (`Client` takes
    no `headers`), so the header path itself is proven in
    `test_identity_http.py` against a real HTTP server and end-to-end in
    `test_identity_live.py`. Here the point is what the TOOLS do with a
    resolved caller, so the request context is faked at the one seam.
    """
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for(handle))


def _bridge_with_locks(tmp_path):
    br = FakeBridge()
    live = {"alice", "bob"}
    br.locks = make_locks_bridge(live_handles=lambda: set(live),
                                 root_fn=lambda: tmp_path)
    return br


# --- the helper's contract ------------------------------------------------


def test_a_resolving_token_overrides_a_wrong_claim(monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    _as(monkeypatch, tokens, "alice")
    handle, verified = verified_handle(tokens, "bob")
    assert (handle, verified) == ("alice", True)


def test_an_absent_token_leaves_the_claim_standing(monkeypatch):
    monkeypatch.setattr("aegis.mcp.identity.caller_token", lambda: None)
    handle, verified = verified_handle(SessionTokens(), "bob")
    assert (handle, verified) == ("bob", False)


def test_an_agreeing_token_is_verified(monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    _as(monkeypatch, tokens, "alice")
    handle, verified = verified_handle(tokens, "alice")
    assert (handle, verified) == ("alice", True)


def test_v1_never_refuses(monkeypatch):
    """The contract for this release: resolve and record, do not reject.
    A caller with no token still gets its claimed handle back."""
    monkeypatch.setattr("aegis.mcp.identity.caller_token", lambda: None)
    handle, _ = verified_handle(SessionTokens(), "bob")
    assert handle == "bob"


# --- the tools actually adopting it ---------------------------------------


@pytest.mark.asyncio
async def test_aegis_claim_records_under_the_token_not_the_argument(
        tmp_path, monkeypatch):
    """The payoff, stated as a behaviour: an agent that passes someone
    else's handle claims under its OWN. Without this the whole feature is
    a helper nobody calls."""
    tokens = SessionTokens()
    tokens.mint("alice")
    _as(monkeypatch, tokens, "alice")
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br, tokens=tokens)

    await _call(srv, "aegis_claim", paths=["src/x.py"], from_handle="bob")

    holders = [c.handle for c in br.locks.active()]
    assert holders == ["alice"], f"claimed as {holders}, not alice"


@pytest.mark.asyncio
async def test_aegis_release_cannot_drop_someone_elses_claim(
        tmp_path, monkeypatch):
    """The second tool, because one adopting the helper says nothing
    about the other three. alice holds a claim; bob asks to release it
    while passing alice's handle — and is resolved back to bob, who does
    not own it."""
    tokens = SessionTokens()
    tokens.mint("alice")
    tokens.mint("bob")
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br, tokens=tokens)

    _as(monkeypatch, tokens, "alice")
    claim = await _call(srv, "aegis_claim", paths=["src/y.py"],
                        from_handle="alice")

    _as(monkeypatch, tokens, "bob")
    out = await _call(srv, "aegis_release",
                      claim_id=claim["claim_id"], from_handle="alice")

    assert out == {"released": False}
    assert [c.handle for c in br.locks.active()] == ["alice"]


@pytest.mark.asyncio
async def test_without_a_token_the_argument_still_governs(
        tmp_path, monkeypatch):
    """v1 never refuses: an out-of-band caller with no token keeps
    exactly today's behaviour."""
    monkeypatch.setattr("aegis.mcp.identity.caller_token", lambda: None)
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br, tokens=SessionTokens())

    await _call(srv, "aegis_claim", paths=["src/z.py"], from_handle="bob")

    assert [c.handle for c in br.locks.active()] == ["bob"]
