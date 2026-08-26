from __future__ import annotations

from aegis.mcp.identity import (
    HEADER, SessionTokens, resolve_caller, verified_handle,
)


def test_header_is_not_authorization():
    """get_http_headers() strips `authorization` by default, so spelling
    the header that way would silently attribute nothing."""
    assert HEADER == "x-aegis-session"
    assert HEADER.lower() != "authorization"


def test_mint_then_resolve_round_trips():
    t = SessionTokens()
    tok = t.mint("alice")
    assert t.resolve(tok) == "alice"


def test_tokens_are_distinct_per_handle():
    t = SessionTokens()
    assert t.mint("alice") != t.mint("bob")


def test_minting_again_replaces_the_previous_token():
    """A respawn supersedes the old subprocess; its token must stop
    resolving or a dead session keeps an identity."""
    t = SessionTokens()
    old = t.mint("alice")
    new = t.mint("alice")
    assert t.resolve(old) is None
    assert t.resolve(new) == "alice"


def test_unknown_and_empty_tokens_resolve_to_none():
    t = SessionTokens()
    t.mint("alice")
    assert t.resolve("nope") is None
    assert t.resolve("") is None
    assert t.resolve(None) is None


def test_token_for_reports_the_live_token():
    t = SessionTokens()
    tok = t.mint("alice")
    assert t.token_for("alice") == tok
    assert t.token_for("bob") is None


def test_rename_keeps_the_token_and_repoints_it():
    """aegis_rename migrates identity atomically. If the token did not
    follow, every call after a rename would go unattributed."""
    t = SessionTokens()
    tok = t.mint("alice")
    t.rename("alice", "carol")
    assert t.resolve(tok) == "carol"
    assert t.token_for("carol") == tok
    assert t.token_for("alice") is None


def test_rename_of_an_unknown_handle_is_a_noop():
    t = SessionTokens()
    t.rename("ghost", "carol")
    assert t.token_for("carol") is None


def test_revoke_stops_resolution():
    t = SessionTokens()
    tok = t.mint("alice")
    t.revoke("alice")
    assert t.resolve(tok) is None
    assert t.token_for("alice") is None


def test_revoke_of_an_unknown_handle_is_a_noop():
    SessionTokens().revoke("ghost")


def test_resolve_caller_outside_a_request_is_none():
    """get_http_headers() returns {} rather than raising when there is no
    active request — tests and in-process callers must not blow up."""
    assert resolve_caller(SessionTokens()) is None
    assert resolve_caller(None) is None


def test_verified_handle_falls_back_to_the_claim():
    handle, verified = verified_handle(SessionTokens(), "alice")
    assert handle == "alice"
    assert verified is False


def test_verified_handle_tolerates_a_missing_claim():
    handle, verified = verified_handle(None, None)
    assert handle == ""
    assert verified is False
