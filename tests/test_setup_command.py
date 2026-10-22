# ruff: noqa: F811  (fixtures imported from test_member_view and test_commands)
"""/setup: the one turn it delivers, and the record each run leaves (#612;
the spec's sections 03 and 07)."""

import json

from aegis import setup

from .conftest import until
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import mcp, turn
from .test_commands import app, send  # noqa: F401
from .test_commands import spawn as spawn_here
from .test_member_view import ADMIN, call, homes, spawn, world  # noqa: F401

REC = {
    "at": "2026-10-20T10:00:00Z",
    "applied": ["queue:tests"],
    "declined": [{"key": "schedule:nightly-audit", "evidence": 3}],
    "sources_until": "2026-10-20T10:00:00Z",
}


def test_a_first_run_sends_the_agent_through_the_whole_page():
    header, body = setup.turn("", "aegis 2.14.0", None, "now")
    assert header == "> from setup · now"
    assert "The person typed /setup with no text" in body
    assert "help_read setup" in body and "in full" in body
    assert "change nothing" in body and "ask before reading transcripts" in body
    assert "no earlier run" in body
    assert body.rstrip().endswith("aegis 2.14.0")


def test_a_second_run_carries_the_record():
    _, body = setup.turn("only schedules", "f", REC, "now")
    assert "The person typed: /setup only schedules" in body
    assert "last run 2026-10-20T10:00:00Z" in body
    assert "queue:tests" in body and "schedule:nightly-audit (3)" in body


def test_from_scratch_ignores_the_record():
    _, body = setup.turn("from scratch", "f", REC, "now")
    assert "ignore the record" in body and "last run" not in body


def test_facts_name_the_harnesses_and_the_config():
    found = [
        {"harness": "claude-code", "bin": "/usr/bin/claude", "version": "2.1.400"},
        {"harness": "codex", "bin": None, "version": None},
    ]
    line = setup.facts("2.14.0", found, ["opus"], ["general"])
    assert line.startswith("aegis 2.14.0")
    assert "harnesses: claude-code 2.1.400" in line and "codex" not in line
    assert "agents: opus" in line and "queues: general" in line
    assert "queues: none" in setup.facts("x", [], ["opus"], [])


def test_the_record_store_keeps_one_file_per_principal(tmp_path):
    rs = setup.Records(tmp_path)
    assert rs.get("ana") is None
    rs.put("ana", {"applied": ["queue:tests"], "declined": [], "sources_until": "t"})
    got = rs.get("ana")
    assert got["applied"] == ["queue:tests"] and got["at"]
    assert (tmp_path / "setup" / "ana.json").exists() and rs.get("ben") is None


def inbox(s) -> list[str]:
    return [
        e["md"]
        for e in s.entries()
        if e["kind"] == "inbox" and "> from setup" in (e.get("md") or "")
    ]


async def test_setup_delivers_one_turn_and_no_session(app):
    lid = await spawn_here(app)
    s = app.sessions.sessions[lid]
    before = len(app.sessions.sessions)
    assert await send(app, lid, "/setup") == {"status": "delivered"}
    await until(lambda: inbox(s), timeout=5)
    (md,) = inbox(s)
    assert "help_read setup" in md and "no earlier run" in md
    assert len(app.sessions.sessions) == before


async def test_a_member_may_run_setup_and_records_are_per_principal(world):
    ben = world.app.sessions.sessions[await spawn(world, BEN)]
    adm = world.app.sessions.sessions[await spawn(world, ADMIN)]
    for s, key in ((ben, "schedule:weekly-prs"), (adm, "queue:tests")):
        said = await turn(
            s,
            mcp("setup_record", applied=[key], declined=[], sources_until="t"),
        )
        assert said.startswith("mcp ok: "), said
    r = await call(world, "session.send", {"log_id": ben.log_id, "text": "/setup"}, BEN)
    assert r == {"status": "delivered"}
    await until(lambda: inbox(ben), timeout=5)
    (md,) = inbox(ben)
    assert "schedule:weekly-prs" in md and "queue:tests" not in md
    files = sorted(p.name for p in (world.app.roots.state_root / "setup").iterdir())
    assert files == sorted(["ben.json", f"{world.app.user}.json"])
    assert json.loads((world.app.roots.state_root / "setup" / files[0]).read_text())
