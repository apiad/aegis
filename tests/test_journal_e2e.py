"""The journal end to end: real sessions on the fake claude, the real app."""

import asyncio
import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from aegis.journal import db, service
from aegis.journal.derive import Row
from aegis.journal.service import COMPLETE, Journal
from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store
from aegis.usage.store import StoredSession

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
    state = _legacy_state(tmp_path, "20260101-000000-abcdef")
    j = Journal(state, None)
    assert j.rebuild() == 3
    assert [r[6] for r in dump(j.path)[0]] == ["session", "turn", "session"]


def _legacy_state(tmp_path, *log_ids) -> Path:
    state = tmp_path / "state"
    (state / "transcripts").mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "session.jsonl"
    for log_id in log_ids:
        (state / "transcripts" / f"{log_id}.jsonl").write_text(fixture.read_text())
    return state


def _complete(path: Path) -> bool:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA application_id").fetchone()[0] == COMPLETE
    finally:
        con.close()


async def test_records_made_during_boot_reach_the_journal(world, tmp_path):
    """sessions.boot() records server_stopped for a session that was working;
    the journal must already be listening."""
    a = await world.spawn()
    f = world.root / "boot.md"
    await turn(a, f"/write {f} => hi")
    log_id = a.log_id
    world.app.journal.flush()
    await world.stop()
    # The server died mid-turn: the result never made it, and the meta says working.
    store = world.app.sessions.store_path(log_id)
    kept = [r for r in read_store(store)[0] if r["i"] <= 5]
    store.write_text("".join(json.dumps(r) + "\n" for r in kept))
    meta = world.app.sessions.metas.path(log_id)
    m = json.loads(meta.read_text())
    meta.write_text(json.dumps({**m, "last_status": "working"}))
    con = sqlite3.connect(world.app.journal.path)
    con.execute(
        "DELETE FROM touches WHERE entry_id IN (SELECT id FROM entries WHERE rec >= 6)"
    )
    con.execute("DELETE FROM entries WHERE rec >= 6")
    con.commit()
    con.close()
    await world.start()
    world.app.journal.flush()
    live = dump(world.app.journal.path)
    assert [r[6] for r in live[0]] == ["session", "turn"]
    assert ("write", str(f.resolve())) in [(t[3], t[4]) for t in live[1]]
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_a_resumed_session_primes_from_its_store(world, tmp_path):
    a = await world.spawn()
    log_id = a.log_id
    await turn(a, f"/write {world.root / 'one.md'} => 1")
    await world.restart()
    b = world.session(log_id)
    await turn(b, f"/write {world.root / 'two.md'} => 2")
    world.app.journal.flush()
    live = dump(world.app.journal.path)
    assert [r[6] for r in live[0]].count("turn") == 2
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_an_interrupted_backfill_resumes_on_the_next_start(tmp_path, monkeypatch):
    state = _legacy_state(tmp_path, "20260101-000000-aaaaaa", "20260101-000000-bbbbbb")
    j = Journal(state, lambda *a: None)
    real = service.stored_sessions

    def stop_after_the_first(root):
        for k, s in enumerate(real(root)):
            if k:
                j._halt.set()
            yield s

    with monkeypatch.context() as m:
        m.setattr(service, "stored_sessions", stop_after_the_first)
        j.start()
        j.flush()
        j.stop()
    first = {r[0] for r in dump(j.path)[0]}
    assert first == {"20260101-000000-aaaaaa"} and not _complete(j.path)
    j2 = Journal(state, lambda *a: None)
    j2.start()
    j2.flush()
    j2.stop()
    assert {r[0] for r in dump(j.path)[0]} == {
        "20260101-000000-aaaaaa",
        "20260101-000000-bbbbbb",
    }
    assert _complete(j.path)
    j3 = Journal(state, lambda *a: None)
    j3.start()
    assert not j3.fresh and j3._q.empty()
    j3.stop()


async def test_a_store_that_raises_does_not_stop_the_others(tmp_path, monkeypatch):
    state = _legacy_state(tmp_path, "20260101-000000-aaaaaa", "20260101-000000-bbbbbb")
    insert = Journal._insert

    def failing(self, con, log_id, rows):
        if log_id.endswith("aaaaaa"):
            raise RuntimeError("boom")
        return insert(self, con, log_id, rows)

    monkeypatch.setattr(Journal, "_insert", failing)
    j = Journal(state, None)
    assert j.rebuild() == 3
    assert {r[0] for r in dump(j.path)[0]} == {"20260101-000000-bbbbbb"}


async def test_stop_ends_a_running_backfill_promptly(tmp_path, monkeypatch):
    def slow(root):
        for k in range(100):
            time.sleep(0.1)
            yield StoredSession(f"s{k}", "h", "claude-code", tmp_path / "none.jsonl")

    monkeypatch.setattr(service, "stored_sessions", slow)
    j = Journal(tmp_path, lambda *a: None)
    j.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    await asyncio.to_thread(j.stop)
    assert time.monotonic() - t0 < 1.5


async def test_flush_returns_when_the_writer_thread_is_dead(tmp_path, monkeypatch):
    real = db.connect
    calls = []

    def connect(path):
        calls.append(path)
        if len(calls) > 1:
            raise sqlite3.OperationalError("disk gone")
        return real(path)

    monkeypatch.setattr(db, "connect", connect)
    j = Journal(tmp_path, lambda *a: None)
    j.start()
    t = threading.Thread(target=j.flush, daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive()


async def test_a_priming_that_raises_leaves_no_half_primed_deriver(
    tmp_path, monkeypatch
):
    j = Journal(tmp_path, None, db_path=tmp_path / "j.db")
    con, _ = db.connect(j.path)

    def boom(path):
        raise OSError("unreadable")

    monkeypatch.setattr(service, "read_store", boom)
    with pytest.raises(OSError):
        j._feed(
            con,
            "x",
            "h",
            tmp_path / "x.jsonl",
            {"i": 3, "ts": 1.0, "src": "aegis"},
            None,
        )
    con.close()
    assert "x" not in j._derivers


def _repo_with_worktree(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.py").write_text("x\n")
    subprocess.run([*g, "add", "a.py"], cwd=repo, check=True)
    subprocess.run([*g, "commit", "-q", "-m", "first"], cwd=repo, check=True)
    wt = repo / ".claude" / "worktrees" / "t"
    subprocess.run(
        [*g, "worktree", "add", "-q", "-b", "topic", str(wt)], cwd=repo, check=True
    )
    return repo, wt


async def test_a_commit_in_a_worktree_is_found_by_the_main_checkout_path(world):
    repo, wt = _repo_with_worktree(world.root)
    a = await world.spawn()
    await turn(
        a,
        f"/sh cd {wt} && echo y > b.py && git add b.py && "
        "git -c user.name=t -c user.email=t@t commit -m 'add b'",
    )
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", path="repo/b.py"))
    assert "add b" in said and "commit" in said
    said = await turn(a, mcp("journal_search", path="repo/elsewhere"))
    assert "0 entries" in said


async def test_a_note_is_searchable_and_survives_a_rebuild(world, tmp_path):
    a = await world.spawn()
    said = await turn(
        a, mcp("journal_note", text="chose sqlite over jsonl", tag="decision")
    )
    assert said == "mcp ok: noted"
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", pattern="sqlite", kind=["note"]))
    assert "decision: chose sqlite over jsonl" in said
    live = dump(world.app.journal.path)
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_an_old_handle_still_finds_the_session(world):
    a = await world.spawn()
    old = a.handle
    world.app.sessions.rename(a.log_id, "renamed-one", None)
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", session=old))
    assert "renamed from" in said and "spawned in" in said


async def test_a_pattern_that_is_not_fts5_is_searched_as_literal_words(world):
    """notes.md and an unbalanced quote do not parse as FTS5; they are retried
    as quoted words, so no pattern ends in bad_pattern."""
    a = await world.spawn()
    await turn(
        a, mcp("journal_note", text="edited notes.md and unclosed", tag="decision")
    )
    world.app.journal.flush()
    for pattern in ("notes.md", '"unclosed', "notes.md unclosed"):
        said = await turn(a, mcp("journal_search", pattern=pattern))
        assert "edited notes.md and unclosed" in said, pattern
    out = await world.app.registry.call(
        "journal.rows", {"pattern": "notes.md", "counts": True}
    )
    assert out["rows"] and out["counts"]


async def test_wildcard_and_near_patterns_that_error_are_searched_as_words(world):
    from typer.testing import CliRunner

    from aegis.cli import app as cli

    a = await world.spawn()
    await turn(a, mcp("journal_note", text="edited x.py today", tag="decision"))
    world.app.journal.flush()
    for pattern in ("*.py", "*", "**", "*a", "*.py today", "NEAR(a b, x)"):
        said = await turn(a, mcp("journal_search", pattern=pattern))
        assert "mcp error" not in said, (pattern, said)
    for pattern in ("*.py", "NEAR(a b, x)"):
        out = CliRunner().invoke(
            cli, ["journal", "search", pattern, "--root", str(world.root)]
        )
        assert out.exit_code == 0, (pattern, out.output)


async def test_a_whitespace_pattern_is_no_pattern(world):
    a = await world.spawn()
    await turn(a, mcp("journal_note", text="some entry", tag="decision"))
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", pattern="   "))
    assert "some entry" in said and "mcp error" not in said


async def test_deliberate_fts5_syntax_is_not_made_literal(world):
    a = await world.spawn()
    await turn(a, mcp("journal_note", text="alpha only", tag="decision"))
    await turn(a, mcp("journal_note", text="beta only", tag="decision"))
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", pattern="alpha OR beta"))
    assert "alpha only" in said and "beta only" in said


async def test_journal_rows_is_for_people_and_note_for_agents(world):
    from aegis.ops import Caller, OpError

    a = await world.spawn()
    with pytest.raises(OpError, match="not_for_agents"):
        await world.app.registry.call("journal.rows", {}, Caller("agent", a.log_id))
    with pytest.raises(OpError, match="agents_only"):
        await world.app.registry.call("journal.note", {"text": "x", "tag": "decision"})
    out = await world.app.registry.call("journal.rows", {"counts": True})
    assert set(out) == {"rows", "more", "counts", "today"}
    # The server's day, which the view shows until the person picks one.
    assert out["today"] == time.strftime("%Y-%m-%d")


async def test_the_cli_searches_and_rebuilds(world):
    from typer.testing import CliRunner

    from aegis.cli import app as cli

    a = await world.spawn()
    await turn(a, mcp("journal_note", text="cli can see this", tag="milestone"))
    world.app.journal.flush()
    runner = CliRunner()
    out = runner.invoke(cli, ["journal", "search", "cli", "--root", str(world.root)])
    assert out.exit_code == 0 and "milestone: cli can see this" in out.output
    await turn(a, mcp("journal_note", text="edited notes.md", tag="decision"))
    world.app.journal.flush()
    out = runner.invoke(
        cli, ["journal", "search", "notes.md", "--root", str(world.root)]
    )
    assert out.exit_code == 0 and "decision: edited notes.md" in out.output
    out = runner.invoke(cli, ["journal", "rebuild", "--root", str(world.root)])
    assert out.exit_code == 0 and out.output.strip().endswith("entries")
    out = runner.invoke(
        cli, ["journal", "search", "--since", "tuesday", "--root", str(world.root)]
    )
    assert out.exit_code == 2 and "not a day" in out.output


async def test_an_interrupted_rebuild_racing_a_backfill_leaves_the_file_incomplete(
    tmp_path, monkeypatch
):
    """The server's backfill must not mark COMPLETE over a table a rebuild cleared."""
    state = _legacy_state(
        tmp_path,
        "20260101-000000-aaaaaa",
        "20260101-000000-bbbbbb",
        "20260101-000000-cccccc",
    )
    real = service.stored_sessions
    after_first, go = threading.Event(), threading.Event()

    def gated(root):
        if threading.current_thread().name == "journal":
            for k, s in enumerate(real(root)):
                if k == 1:
                    after_first.set()
                    go.wait(10)
                yield s
        else:  # the CLI, interrupted right after its clear
            raise KeyboardInterrupt
            yield

    monkeypatch.setattr(service, "stored_sessions", gated)
    server = Journal(state, lambda *a: None)
    server.start()
    assert after_first.wait(10)
    with pytest.raises(KeyboardInterrupt):
        Journal(state, None).rebuild()
    go.set()
    server.flush()
    server.stop()
    assert not _complete(server.path)
    monkeypatch.setattr(service, "stored_sessions", real)
    nxt = Journal(state, lambda *a: None)
    nxt.start()
    nxt.flush()
    nxt.stop()
    assert _complete(server.path) and len({r[0] for r in dump(server.path)[0]}) == 3


async def test_a_corrupt_journal_file_is_set_aside_and_backfilled(tmp_path):
    state = _legacy_state(tmp_path, "20260101-000000-aaaaaa")
    (state / "journal.db").write_bytes(b"this is not a database" * 100)
    j = Journal(state, lambda *a: None)
    j.start()
    j.flush()
    j.stop()
    assert j.fresh and _complete(j.path)
    assert {r[0] for r in dump(j.path)[0]} == {"20260101-000000-aaaaaa"}
    assert len(list(state.glob("journal.db.corrupt-*"))) == 1


def _git_repo(path: Path, text: str) -> str:
    """A repo at path with one commit; returns its hash."""
    path.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q", "-b", "main"], cwd=path, check=True)
    (path / "c.py").write_text(text)
    subprocess.run([*g, "add", "c.py"], cwd=path, check=True)
    subprocess.run([*g, "commit", "-q", "-m", "c"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


def test_a_commit_not_in_the_guessed_repo_is_found_where_the_turn_wrote(tmp_path):
    h = _git_repo(tmp_path / "real", "real\n")
    _git_repo(tmp_path / "guessed", "another\n")
    j = Journal(tmp_path, None, db_path=tmp_path / "j.db")
    guessed, real = str(tmp_path / "guessed"), str((tmp_path / "real").resolve())
    near = Row(1, 1.0, "commit", "c", "h", commit=(guessed, h), near=(real,))
    e = j._entry("log", 0, near)
    assert (e.repo, e.files_unknown) == (real, False)
    assert [(op, rel) for op, _, rel, _ in e.touches] == [("commit", "c.py")]
    # Nowhere to be found: no repo, rather than the wrong one.
    lost = Row(1, 1.0, "commit", "c", "h", commit=(guessed, h))
    e = j._entry("log", 0, lost)
    assert (e.repo, e.files_unknown, e.touches) == (None, True, [])


async def test_a_heredoc_commit_from_outside_the_repo_names_its_files(world, tmp_path):
    """Claude's standard commit form, from a session spawned above the repo;
    and a commit whose call then fails. Both name their files, live and rebuilt."""
    repo, _ = _repo_with_worktree(world.root)
    a = await world.spawn()
    git = "git -c user.name=t -c user.email=t@t"
    await turn(
        a,
        f"/sh cd {repo} && echo y > c.py && git add c.py && {git} commit -m "
        "\"$(cat <<'EOF'\nadd c\n\nCo-Authored-By: t\nEOF\n)\"",
    )
    await turn(
        a,
        f"/sh cd {repo} && echo z > d.py && git add d.py && {git} commit -m 'add d'"
        " && git push nowhere main",
    )
    world.app.journal.flush()
    for f, subject in (("c.py", "add c"), ("d.py", "add d")):
        said = await turn(a, mcp("journal_search", path=f"repo/{f}", kind=["commit"]))
        assert subject in said and "1 entries" in said, said
    live = dump(world.app.journal.path)
    again = Journal(world.app.roots.state_root, None, db_path=tmp_path / "again.db")
    again.rebuild()
    assert dump(tmp_path / "again.db") == live


async def test_the_cli_reads_a_relative_path_from_the_shells_directory(
    world, monkeypatch
):
    from typer.testing import CliRunner

    from aegis.cli import app as cli

    (world.root / "sub").mkdir()
    a = await world.spawn()
    await turn(a, f"/write {world.root / 'sub' / 'a.py'} => x")
    world.app.journal.flush()
    monkeypatch.chdir(world.root / "sub")
    out = CliRunner().invoke(
        cli, ["journal", "search", "--path", "a.py", "--root", str(world.root)]
    )
    assert out.exit_code == 0 and "1 entries" in out.output, out.output


def test_the_cli_rebuilds_over_a_corrupt_file_and_refuses_an_unknown_kind(tmp_path):
    from typer.testing import CliRunner

    from aegis.cli import app as cli

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    state = tmp_path / ".aegis" / "state"
    (state / "transcripts").mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "session.jsonl"
    (state / "transcripts" / "20260101-000000-abcdef.jsonl").write_text(
        fixture.read_text()
    )
    (state / "journal.db").write_bytes(b"this is not a database" * 100)
    runner = CliRunner()
    out = runner.invoke(cli, ["journal", "rebuild", "--root", str(tmp_path)])
    assert out.exit_code == 0 and out.output.strip() == "3 entries", out.output
    assert len(list(state.glob("journal.db.corrupt-*"))) == 1
    out = runner.invoke(
        cli, ["journal", "search", "--kind", "commits", "--root", str(tmp_path)]
    )
    assert out.exit_code == 2 and "commits" in out.output, out.output


async def test_an_entry_from_before_a_rename_names_the_current_handle(world):
    """peer_read takes an open session's current handle, not the one an old
    entry carries."""
    a = await world.spawn()
    old = a.handle
    await turn(a, mcp("journal_note", text="before the rename", tag="decision"))
    world.app.sessions.rename(a.log_id, "renamed-one", None)
    world.app.journal.flush()
    said = await turn(a, mcp("journal_search", pattern="before the rename"))
    assert f"{old} (now renamed-one)  note" in said, said
