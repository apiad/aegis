# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Claude sign-in (claude/login.py): the setup-token every Claude Code process
gets, the card a failed sign-in raises, and the quota windows read from the
stream when the usage endpoint refuses that token."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from aegis.claude import login
from aegis.claude.stream import RateLimit, Result, parse
from aegis.ops import OpError
from aegis.quota.claude import parse_stream
from aegis.quota.core import QuotaError, QuotaService

from .conftest import until
from .test_agents import inbox, turn, world  # noqa: F401  (fixture)

GOOD = "sk-ant-oat01-" + "A1b2_C3d4-" * 9
BAD = "sk-ant-oat01-bad" + "x" * 80


def token_file() -> Path:
    return Path(os.environ["AEGIS_CLAUDE_TOKEN_FILE"])


def put(value: str, mode: int = 0o600) -> Path:
    p = token_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value + "\n")
    p.chmod(mode)
    return p


# -- the token file ----------------------------------------------------------------
def test_the_browser_code_and_other_shapes_are_refused_with_what_to_do():
    assert "code from the browser" in login.shape_error("abc123#state456")
    assert login.PREFIX in login.shape_error("sk-ant-api03-whatever")
    assert login.shape_error("") == "Paste the token first."
    assert login.shape_error(GOOD) is None
    assert login.shape_error(f"  {GOOD}\n") is None


def test_only_a_private_well_formed_file_is_read():
    assert login.read_token() is None  # no file: the stored sign-in is used
    put(GOOD, 0o644)
    assert login.read_token() is None
    assert "0600" in login.file_problem(token_file())
    put("not a token")
    assert login.read_token() is None
    put(GOOD)
    assert login.read_token() == GOOD and login.file_problem(token_file()) is None


def test_the_token_reaches_the_servers_own_children_only(monkeypatch):
    put(GOOD)
    monkeypatch.delenv(login.VAR, raising=False)
    assert login.with_token({"A": "1"}, None) == {"A": "1", login.VAR: GOOD}
    assert login.with_token({"A": "1"}, "ben") == {"A": "1"}
    assert login.with_token({login.VAR: "pool"}, None) == {login.VAR: "pool"}
    assert login.with_token(None, None)[login.VAR] == GOOD
    token_file().unlink()
    assert login.with_token(None, None) is None


def test_a_written_token_is_0600_in_a_0700_directory(tmp_path):
    p = login.write_token(GOOD, tmp_path / "new" / "tok")
    assert p.read_text() == GOOD + "\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert [x.name for x in p.parent.iterdir()] == ["tok"]


# -- the stream --------------------------------------------------------------------
def test_a_401_result_is_a_failed_sign_in_and_a_403_is_not():
    line = {"type": "result", "subtype": "success", "is_error": True, "result": "x"}
    (r,) = parse(json.dumps(line | {"api_error_status": 401}))
    assert isinstance(r, Result) and r.auth_failed
    (r,) = parse(json.dumps(line | {"api_error_status": 403}))
    assert not r.auth_failed
    (r,) = parse(json.dumps(line | {"result": "Not logged in · Please run /login"}))
    assert r.auth_failed
    (r,) = parse(json.dumps({**line, "is_error": False, "api_error_status": 401}))
    assert not r.auth_failed


def test_a_rate_limit_event_becomes_session_and_weekly_windows():
    line = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": 0.12, "resetsAt": 1792768800},
                "seven_day": {"utilization": 0.04, "resetsAt": 1793340000},
                "something_new": {"utilization": 0.5},
            },
        },
    }
    (ev,) = parse(json.dumps(line))
    assert isinstance(ev, RateLimit)
    snap = parse_stream(ev.windows, now=5.0)
    assert snap.fetched_at == 5.0
    assert snap.window("session").percent == 12.0
    assert snap.window("weekly_all").percent == 4.0
    assert snap.window("session").resets_at.timestamp() == 1792768800
    assert len(snap.windows) == 2
    assert parse_stream({"five_hour": {"utilization": "high"}}, now=0) is None


async def test_the_endpoints_refusal_does_not_hide_a_stream_reading(tmp_path):
    def refused(token):
        raise QuotaError("unauthorized")

    svc = QuotaService(
        fetch=refused, token_reader=lambda: "setup-token", clock=lambda: 100.0
    )
    await svc.refresh(force=True)
    assert svc.current().failure == "unauthorized"
    svc.observe(parse_stream({"five_hour": {"utilization": 0.3}}, now=100.0))
    await svc.refresh(force=True)
    state = svc.current()
    assert state.failure == "" and state.snapshot.window("session").percent == 30.0
    gone = QuotaService(fetch=refused, token_reader=lambda: None, clock=lambda: 1.0)
    gone.observe(parse_stream({"five_hour": {"utilization": 0.3}}, now=1.0))
    await gone.refresh(force=True)
    assert gone.current().snapshot is not None


# -- the app -----------------------------------------------------------------------
@pytest.fixture
def toasts(world, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(
        world.app.sessions.sign_in, "_toast", lambda text, s: said.append(text)
    )
    return said


def cards(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "claude_login"]


async def test_a_failed_sign_in_raises_one_card_and_a_new_token_restarts_both(
    world, toasts, caplog
):
    caplog.set_level(logging.DEBUG)
    put(BAD)
    a, b = await world.spawn(), await world.spawn()
    await turn(a, "hello")
    await turn(b, "hello")
    await until(lambda: cards(a), what="the card")
    (card,) = cards(a)
    assert cards(b) == [] and len(toasts) == 1
    await until(
        lambda: len(cards(a)[0]["detail"]["handles"]) == 2, what="b joins the card"
    )
    assert cards(a)[0]["detail"]["handles"] == [a.handle, b.handle]
    assert world.app.sessions.card(a)["attention_line"] == "Claude sign-in failed"

    call = world.app.registry.call
    with pytest.raises(OpError) as err:
        await call(
            "claude.token",
            {"log_id": a.log_id, "id": card["id"], "secret": "c0de#st4te"},
        )
    assert "code from the browser" in err.value.message
    with pytest.raises(OpError) as err:
        await call(
            "claude.token", {"log_id": a.log_id, "id": card["id"], "secret": BAD}
        )
    assert "401" in err.value.message and login.read_token() == BAD

    got = await call(
        "claude.token", {"log_id": a.log_id, "id": card["id"], "secret": GOOD}
    )
    assert sorted(got["restarted"]) == sorted([a.handle, b.handle])
    assert login.read_token() == GOOD
    assert stat.S_IMODE(token_file().stat().st_mode) == 0o600
    assert cards(a)[0]["status"] == "renewed"
    for s in (a, b):
        await until(lambda s=s: inbox(s), timeout=8, what="the renewed message")
        assert "aegis:login · renewed" in json.dumps(inbox(s), ensure_ascii=False)
    everything = json.dumps([a.entries(), b.entries()]) + caplog.text
    assert GOOD not in everything and BAD not in everything


async def test_a_turns_rate_limit_event_moves_the_claude_gauges(world, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_RATE_LIMIT", "0.37")
    a = await world.spawn()
    await turn(a, "hello")

    def session_percent():
        for p in world.app.quota.snapshot()["providers"]:
            if p["name"] == "claude":
                for w in p["windows"]:
                    if w["kind"] == "session":
                        return w["percent"]
        return None

    await until(lambda: session_percent() == 37.0, what="the gauge")
