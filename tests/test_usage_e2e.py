"""Usage rows end to end: real sessions on the fake claude, the real app."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from aegis.journal import db
from aegis.journal.service import Journal
from aegis.usage import tables

from .test_agents import CONFIG, World, turn
from .test_journal_e2e import _legacy_state

USAGE = ("usage_turns", "usage_parts", "usage_waits", "usage_ctx", "usage_whatif")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def dump(path: Path) -> dict:
    con = sqlite3.connect(path)
    try:
        return {
            t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3").fetchall()
            for t in USAGE
        }
    finally:
        con.close()


def _repo(root: Path) -> Path:
    repo = root / "atlas"
    repo.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*g, "remote", "add", "origin", "git@github.com:acme-co/atlas.git"],
                   cwd=repo, check=True)  # fmt: skip
    return repo


async def test_live_usage_rows_equal_a_rebuild(world, tmp_path):
    repo = _repo(world.root)
    a = await world.spawn()
    await turn(a, f"/sh cat {repo}/missing.txt; ls {repo}")
    await turn(a, f"/write {repo / 'a.py'} => x = 1")
    await turn(a, f"/edit {repo / 'a.py'} 1 => 2")
    await turn(
        a,
        f"/sh cd {repo} && git add a.py && "
        "git -c user.name=t -c user.email=t@t commit -m 'fix: x'",
    )
    await turn(a, "thanks")
    world.app.journal.flush()
    live = dump(world.app.journal.path)
    assert len(live["usage_turns"]) == 5
    parts = live["usage_parts"]
    assert {p[6] for p in parts} >= {"reading", "editing", "committing", "talking"}
    edits = [p for p in parts if p[6] == "editing"]
    assert edits and all(p[5] == "fix" and p[7] == 1 for p in edits)
    assert {p[4] for p in edits} == {str(repo.resolve())}
    con = sqlite3.connect(world.app.journal.path)
    assert con.execute("SELECT owner FROM repo_owners").fetchall() == [("acme-co",)]
    con.close()
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_a_complete_journal_without_usage_tables_backfills_usage_only(tmp_path):
    state = _legacy_state(tmp_path, "20260101-000000-abcdef")
    j = Journal(state, None)
    j.rebuild()
    con = sqlite3.connect(j.path)
    entries = con.execute("SELECT count(*) FROM entries").fetchone()[0]
    for t in tables.TABLES:
        con.execute(f"DROP TABLE {t}")
    con.commit()
    con.close()
    j = Journal(state, lambda *a: None)
    j.start()
    j.flush()
    j.stop()
    con = sqlite3.connect(j.path)
    try:
        assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == entries
        assert con.execute("SELECT count(*) FROM usage_turns").fetchone()[0] > 0
        assert tables.is_complete(con)
    finally:
        con.close()
    assert j.usage_progress() == {}


async def test_a_store_without_a_meta_file_still_derives(tmp_path):  # review focus 4
    state = _legacy_state(tmp_path, "20260101-000000-abcdef")
    assert not (state / "sessions").exists()
    j = Journal(state, None, meta_of=lambda log_id: {})
    j.rebuild()
    con = sqlite3.connect(j.path)
    try:
        rows = con.execute("SELECT person, kind FROM usage_turns").fetchall()
    finally:
        con.close()
    assert rows and set(rows) == {("", "foreground")}


def _snap(percent: float, read_at: int = 1_000_000) -> dict:
    return {
        "providers": [
            {"name": "claude", "harness": "claude-code", "account": "a1b2",
             "read_at": read_at, "windows": [
                 {"kind": "weekly_all", "percent": percent, "resets_at": 2_000_000},
                 {"kind": "session", "percent": 3.0, "resets_at": 1_010_000}]},
            {"name": "opencode-go", "harness": "opencode", "account": None,
             "read_at": 1_000_000, "windows": [{"kind": "weekly", "percent": 1.0}]},
        ]
    }  # fmt: skip


async def test_a_quota_reading_is_kept_only_when_it_changed(tmp_path):
    (tmp_path / "transcripts").mkdir()
    j = Journal(tmp_path, lambda *a: None)
    j.start()
    for k in range(3):
        j.quota(_snap(10.0, 1_000_000 + 60 * k))
    j.quota(_snap(12.5, 1_000_200))
    j.flush()
    j.stop()
    con = sqlite3.connect(j.path)
    try:
        rows = con.execute(
            "SELECT billing, window, percent FROM usage_quota ORDER BY window, percent"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        ("claude:a1b2", "session", 3.0),
        ("claude:a1b2", "weekly_all", 10.0),
        ("claude:a1b2", "weekly_all", 12.5),
    ]


def test_the_journal_reads_what_finished_windows_spent(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    j = Journal(state, None)
    assert j.usual_spend("claude:a", "session", 18000.0) is None  # no file yet
    con, _ = db.connect(j.path)
    tables.ensure(con)
    con.execute("INSERT INTO usage_quota VALUES('claude:a', 'session', 1.0, 40.0, 2.0)")
    con.close()
    assert j.usual_spend("claude:a", "session", 18000.0) == 40.0
