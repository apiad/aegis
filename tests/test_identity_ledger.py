from __future__ import annotations

from aegis.comms.middleware import CommsMiddleware
from aegis.comms.persistence import CommsLedger
from aegis.mcp.identity import SessionTokens


def _mw(tmp_path, tokens=None):
    return CommsMiddleware(CommsLedger(tmp_path), tokens=tokens)


def _rows(tmp_path):
    # read() is day-scoped and the day comes off the envelope's own ts;
    # read_all() spares the tests from restating it.
    return CommsLedger(tmp_path).read_all()


def test_a_resolved_token_attributes_a_tool_that_takes_no_from_handle(
        tmp_path, monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    mw = _mw(tmp_path, tokens)
    mw._record("aegis_list_sessions", {}, "c1", "2026-08-26T00:00:00Z",
               0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["alice"]


def test_the_token_wins_over_a_wrong_from_handle(tmp_path, monkeypatch):
    tokens = SessionTokens()
    tokens.mint("alice")
    monkeypatch.setattr("aegis.mcp.identity.caller_token",
                        lambda: tokens.token_for("alice"))
    mw = _mw(tmp_path, tokens)
    mw._record("aegis_handoff", {"from_handle": "bob"}, "c1",
               "2026-08-26T00:00:00Z", 0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["alice"]


def test_without_a_token_the_argument_still_attributes(tmp_path):
    mw = _mw(tmp_path, SessionTokens())
    mw._record("aegis_handoff", {"from_handle": "bob"}, "c1",
               "2026-08-26T00:00:00Z", 0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == ["bob"]


def test_with_neither_the_row_stays_honestly_unattributed(tmp_path):
    mw = _mw(tmp_path, SessionTokens())
    mw._record("aegis_list_sessions", {}, "c1", "2026-08-26T00:00:00Z",
               0.0, "ok", None)
    assert [r["from"] for r in _rows(tmp_path)] == [""]


def test_the_runtime_owns_a_token_store():
    from aegis.mcp.runtime import AegisMCP
    assert AegisMCP().tokens.resolve("nope") is None
