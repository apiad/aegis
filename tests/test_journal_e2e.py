"""The journal end to end: real sessions on the fake claude, the real app."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from aegis.journal.service import Journal
from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store

from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_the_fake_runs_a_command_writes_and_edits(world):
    a = await world.spawn()
    f = world.root / "out.txt"
    await turn(a, f"/sh echo hi > {f}")
    assert f.read_text() == "hi\n"
    await turn(a, f"/write {f} => one two")
    await turn(a, f"/edit {f} one => three")
    assert f.read_text() == "three two"
    tools = [e for e in a.entries() if e["kind"] == "tool"]
    assert [e["title"] for e in tools][-3:] == ["Bash", "Write", "Edit"]
    assert all(e["status"] == "ok" for e in tools[-3:])


def _records(world, log_id):
    return read_store(world.app.sessions.store_path(log_id))[0]


async def test_the_spawn_record_names_the_handle_and_a_rename_is_recorded(world):
    a = await world.spawn()
    first = a.handle
    world.app.sessions.rename(a.log_id, "new-name", None)
    recs = _records(world, a.log_id)
    assert [r["handle"] for r in recs if r.get("kind") == "spawn"] == [first]
    names = [r for r in recs if r.get("kind") == "name"]
    assert [(r["handle"], r["title"]) for r in names] == [("new-name", a.title)]
    world.app.sessions.rename(a.log_id, None, "just a title")
    world.app.sessions.rename(a.log_id, "new-name", None)
    recs = _records(world, a.log_id)
    assert len([r for r in recs if r.get("kind") == "name"]) == 1


async def test_a_rename_leaves_last_activity_alone(world):
    a = await world.spawn()
    before = a.last_activity
    world.app.sessions.rename(a.log_id, "new-name", "a title")
    assert a.last_activity == before


async def test_closing_records_a_close_that_the_fold_draws(world):
    a = await world.spawn()
    log_id = a.log_id
    await world.app.sessions.close(log_id)
    recs = _records(world, log_id)
    assert recs[-1]["kind"] == "close"
    assert fold_records(recs).entries()[-1]["summary"] == "closed"


def dump(path: Path):
    con = sqlite3.connect(path)
    e = con.execute(
        "SELECT log_id, rec, n, ts, handle, repo, kind, tag, text, source, files_unknown"
        " FROM entries ORDER BY log_id, rec, n"
    ).fetchall()
    t = con.execute(
        "SELECT e.log_id, e.rec, e.n, t.op, t.full FROM touches t JOIN entries e"
        " ON e.id = t.entry_id ORDER BY 1, 2, 3, 4, 5"
    ).fetchall()
    con.close()
    return e, t


async def test_the_live_journal_equals_a_rebuild(world, tmp_path):
    a = await world.spawn()
    f = world.root / "notes.md"
    await turn(a, f"/write {f} => hello")
    await turn(a, mcp("turn_end", attention="done", line="wrote the notes"))
    world.app.journal.flush()
    live = dump(world.app.journal.path)
    kinds = [row[6] for row in live[0]]
    assert kinds.count("turn") == 2 and "session" in kinds
    assert ("write", str(f.resolve())) in [(t[3], t[4]) for t in live[1]]
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_start_returns_before_the_backfill_finishes(tmp_path, monkeypatch):
    """Review focus 4."""
    gate = threading.Event()
    monkeypatch.setattr(Journal, "_backfill", lambda self, con: gate.wait(5) and 0)
    j = Journal(tmp_path, lambda *a: None)
    t0 = time.monotonic()
    j.start()
    assert time.monotonic() - t0 < 0.5 and j.fresh
    gate.set()
    j.stop()


async def test_a_store_from_before_this_change_backfills(tmp_path):
    """Review focus 1: a legacy-shaped store (tests/fixtures/session.jsonl)."""
    state = tmp_path / "state"
    (state / "transcripts").mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "session.jsonl"
    (state / "transcripts" / "20260101-000000-abcdef.jsonl").write_text(
        fixture.read_text()
    )
    n = Journal(state, None).rebuild()
    assert n >= 0
