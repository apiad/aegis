"""aegis usage import: Claude Code's own session files as mode outside."""

import json
import subprocess
from pathlib import Path

import pytest

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.importer import ImportReport, aegis_known, import_claude_projects

from .stores import assistant, epoch, store

SID = "0b1c2d3e-0000-4000-8000-000000000001"


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    return path


def prompt(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def project_file(
    root: Path, sid: str, cwd: Path, lines: list[tuple[str, dict]], mode: str = "w"
) -> Path:
    """A session file as Claude Code writes it under ~/.claude/projects."""
    d = root / str(cwd).replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    with f.open(mode) as out:
        for ts, obj in lines:
            rec = {**obj, "timestamp": ts, "cwd": str(cwd), "sessionId": sid}
            out.write(json.dumps(rec) + "\n")
    return f


def _con(tmp_path: Path):
    con, _ = db.connect(tmp_path / "state" / "journal.db")
    tables.ensure(con)
    return con


def _run(con, roots, known=frozenset(), progress=lambda done, total: None):
    return import_claude_projects(con, roots, set(known), "", progress)


def _out(con) -> int:
    return con.execute("SELECT COALESCE(SUM(tok_out), 0) FROM usage_turns").fetchone()[
        0
    ]


@pytest.fixture
def con(tmp_path):
    (tmp_path / "state").mkdir()
    c = _con(tmp_path)
    yield c
    c.close()


def test_a_project_file_is_one_outside_session_in_its_repo(tmp_path, con):
    repo = _repo(tmp_path / "repo")
    root = tmp_path / "projects"
    project_file(
        root,
        SID,
        repo,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
            ("2026-10-01T10:01:00Z", prompt("second")),
            ("2026-10-01T10:01:05Z", assistant("m2")),
        ],
    )
    r = _run(con, [root])
    assert isinstance(r, ImportReport)
    assert r.sessions == 1
    turns = con.execute("SELECT log_id, mode, tok_out FROM usage_turns").fetchall()
    assert turns == [(f"cc-{SID}", "outside", 20), (f"cc-{SID}", "outside", 20)]
    repos = {p[0] for p in con.execute("SELECT repo FROM usage_parts")}
    assert repos == {str(repo)}


def test_a_message_an_aegis_store_holds_is_skipped(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    store(
        tmp_path / "state",
        "a1",
        cwd,
        [("2026-10-01T10:00:05Z", assistant("m1"))],
    )
    root = tmp_path / "projects"
    project_file(
        root,
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
            ("2026-10-01T10:00:06Z", assistant("m2", output_tokens=7)),
        ],
    )
    known = aegis_known(tmp_path / "state")
    assert "m1" in known
    _run(con, [root], known)
    assert _out(con) == 7


def test_a_session_aegis_ran_itself_is_not_imported(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    init = {"type": "system", "subtype": "init", "session_id": SID, "cwd": str(cwd)}
    store(
        tmp_path / "state",
        "a1",
        cwd,
        [("2026-10-01T10:00:00Z", init), ("2026-10-01T10:00:05Z", assistant("m1"))],
    )
    root = tmp_path / "projects"
    # Lines the aegis store has not caught up with yet: still aegis's session.
    project_file(
        root,
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
            ("2026-10-01T10:00:09Z", assistant("m9")),
        ],
    )
    r = _run(con, [root], aegis_known(tmp_path / "state"))
    assert (r.sessions, r.live) == (0, 1)
    assert _out(con) == 0


def test_importing_twice_counts_nothing_twice(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    root = tmp_path / "projects"
    project_file(
        root,
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
        ],
    )
    assert _run(con, [root]).sessions == 1
    again = _run(con, [root])
    assert (again.sessions, again.turns, again.already) == (0, 0, 1)
    assert _out(con) == 20
    # A resumed session's file repeats earlier messages: they count once.
    project_file(
        root,
        "0b1c2d3e-0000-4000-8000-000000000002",
        cwd,
        [
            ("2026-10-02T10:00:00Z", prompt("again")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
            ("2026-10-02T10:00:05Z", assistant("m3")),
        ],
    )
    _run(con, [root])
    assert _out(con) == 40


def test_a_file_that_grew_is_read_again_from_where_it_was(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    root = tmp_path / "projects"
    first = [
        ("2026-10-01T10:00:00Z", prompt("first")),
        ("2026-10-01T10:00:05Z", assistant("m1")),
    ]
    project_file(root, SID, cwd, first)
    _run(con, [root])
    # The last turn goes on, then a new one starts.
    more = [
        ("2026-10-01T10:00:08Z", assistant("m2", output_tokens=5)),
        ("2026-10-01T10:02:00Z", prompt("second")),
        ("2026-10-01T10:02:05Z", assistant("m3", output_tokens=3)),
    ]
    project_file(root, SID, cwd, more, mode="a")
    r = _run(con, [root])
    assert (r.sessions, r.already) == (1, 0)
    assert _out(con) == 28
    n = con.execute("SELECT COUNT(*) FROM usage_turns").fetchone()[0]
    assert n == 2


def test_a_run_stopped_half_way_resumes(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    root = tmp_path / "projects"
    for k in range(3):
        project_file(
            root,
            f"0b1c2d3e-0000-4000-8000-00000000001{k}",
            cwd,
            [
                (f"2026-10-0{k + 1}T10:00:00Z", prompt("go")),
                (f"2026-10-0{k + 1}T10:00:05Z", assistant(f"m{k}")),
            ],
        )

    def stop(done, total):
        if done == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(con, [root], progress=stop)
    assert _out(con) == 20
    seen = []
    r = _run(con, [root], progress=lambda done, total: seen.append((done, total)))
    assert (r.sessions, r.already) == (2, 1)
    assert seen[-1] == (3, 3)
    assert _out(con) == 60


def test_a_turn_takes_the_time_of_its_lines(tmp_path, con):
    cwd = tmp_path / "work"
    cwd.mkdir()
    root = tmp_path / "projects"
    project_file(
        root,
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:30Z", assistant("m1")),
        ],
    )
    _run(con, [root])
    ts, secs = con.execute("SELECT ts, secs FROM usage_turns").fetchone()
    assert ts == epoch("2026-10-01T10:00:30Z")
    assert secs == 30


def test_the_command_imports_once(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from aegis.cli import app

    cwd = tmp_path / "work"
    cwd.mkdir()
    project_file(
        tmp_path / "home" / ".claude" / "projects",
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
        ],
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    args = ["usage", "import", "--claude-projects", "--state", str(tmp_path / "s")]
    (tmp_path / "s").mkdir()
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "1 sessions (1 turns) imported" in first.output
    again = CliRunner().invoke(app, args)
    assert "0 sessions (0 turns) imported, 1 files already imported" in again.output
    assert CliRunner().invoke(app, ["usage", "import"]).exit_code == 2


def test_a_rebuild_keeps_what_was_imported(tmp_path, con):
    from aegis.journal.service import Journal

    cwd = tmp_path / "work"
    cwd.mkdir()
    root = tmp_path / "projects"
    project_file(
        root,
        SID,
        cwd,
        [
            ("2026-10-01T10:00:00Z", prompt("first")),
            ("2026-10-01T10:00:05Z", assistant("m1")),
        ],
    )
    _run(con, [root])
    Journal(tmp_path / "state", None).rebuild()
    assert _out(con) == 20
    assert _run(con, [root]).already == 1
