"""The journal knows each touch's worktree, each commit's full hash, a PR's repo,
and reports a session's writes before its turn closes (the Repos row's needs)."""

import os
import subprocess
from pathlib import Path

from aegis.journal import db
from aegis.journal.derive import Deriver, Row
from aegis.journal.service import Journal

from .test_fold import Rec

G = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _repo_with_worktree(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run([*G, "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a.py").write_text("x\n")
    subprocess.run([*G, "add", "a.py"], cwd=repo, check=True)
    subprocess.run([*G, "commit", "-q", "-m", "first"], cwd=repo, check=True)
    wt = root / "wt"
    subprocess.run(
        [*G, "worktree", "add", "-q", "-b", "topic", str(wt)], cwd=repo, check=True
    )
    return repo.resolve(), wt.resolve()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text("y\n")
    subprocess.run([*G, "add", name], cwd=path, check=True)
    subprocess.run([*G, "commit", "-q", "-m", f"add {name}"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


def test_a_touch_in_a_worktree_keeps_its_top(tmp_path):
    repo, wt = _repo_with_worktree(tmp_path)
    j = Journal(tmp_path, None, db_path=tmp_path / "j.db")
    row = Row(1, 1.0, "turn", "t", "h", touches=[("write", str(wt / "b.py"))])
    e = j._entry("log", 0, row)
    assert e.repo == str(repo)
    assert e.touches == [("write", str(repo), "b.py", str(repo / "b.py"), str(wt))]
    assert e.top == str(wt)


def test_a_commit_entry_stores_its_full_sha_and_top(tmp_path):
    repo, wt = _repo_with_worktree(tmp_path)
    h = _commit(wt, "c.py")
    j = Journal(tmp_path, None, db_path=tmp_path / "j.db")
    e = j._entry("log", 0, Row(1, 1.0, "commit", "c", "h", commit=(str(wt), h[:7])))
    assert (e.repo, e.top, e.sha) == (str(repo), str(wt), h)
    assert [(op, rel, top) for op, _, rel, _, top in e.touches] == [
        ("commit", "c.py", str(wt))
    ]


def test_a_pr_row_takes_the_repo_of_its_workdir(tmp_path):
    repo, wt = _repo_with_worktree(tmp_path)
    j = Journal(tmp_path, None, db_path=tmp_path / "j.db")
    e = j._entry("log", 0, Row(1, 1.0, "pr", "merged #5", "h", workdir=str(wt)))
    assert (e.repo, e.top) == (str(repo), str(wt))
    outside = j._entry(
        "log", 0, Row(1, 1.0, "pr", "merged #5", "h", workdir=str(tmp_path))
    )
    assert (outside.repo, outside.top) == (None, None)


def test_top_and_sha_are_stored(tmp_path):
    con, fresh = db.connect(tmp_path / "j.db")
    assert fresh and db.SCHEMA_VERSION == 2
    e = db.Entry(
        "L",
        1,
        0,
        1.0,
        "h",
        "/r",
        "commit",
        "",
        "c",
        None,
        False,
        [("commit", "/r", "a", "/r/a", "/wt"), ("edit", "/r", "b", "/r/b")],
        top="/wt",
        sha="abc",
    )
    db.insert(con, e)
    assert con.execute("SELECT top, sha FROM entries").fetchall() == [("/wt", "abc")]
    assert sorted(con.execute("SELECT path, top FROM touches").fetchall()) == [
        ("a", "/wt"),
        ("b", None),
    ]


def test_a_schema_1_file_is_reset(tmp_path):
    con, _ = db.connect(tmp_path / "j.db")
    con.execute("PRAGMA user_version=1")
    con.close()
    con, fresh = db.connect(tmp_path / "j.db")
    assert fresh
    cols = [r[1] for r in con.execute("PRAGMA table_info(touches)")]
    assert "top" in cols


def test_pending_lists_the_open_turns_writes_before_it_closes():
    r = Rec()
    r.own("spawn", agent="opus", model="opus", cwd="/w", handle="h")
    r.own("send", text="go")
    r.call("t1", "Write", {"file_path": "a.py", "content": "z"})
    r.output("t1", "ok")
    r.call("t2", "Bash", {"command": "echo x > /w/b.txt"})
    r.output("t2", "")
    d = Deriver("h")
    for rec in r.records:
        d.feed(rec)
    assert d.pending() == ["/w/a.py", "/w/b.txt"]
    r.result()
    d.feed(r.records[-1])
    assert d.pending() == []


def test_on_live_reports_new_writes_and_the_dirs_of_commits_prs_and_pushes(tmp_path):
    seen = []
    j = Journal(
        tmp_path, None, db_path=tmp_path / "j.db", on_live=lambda *a: seen.append(a)
    )
    con, _ = db.connect(j.path)
    r = Rec()
    r.own("spawn", agent="opus", model="opus", cwd=str(tmp_path), handle="h")
    r.own("send", text="go")
    r.call("t1", "Write", {"file_path": "a.py", "content": "z"})
    r.output("t1", "ok")
    store = tmp_path / "none.jsonl"
    store.write_text("")
    for rec in r.records:
        j._feed(con, "L", "h", store, rec, None)
    assert seen == [("L", "h", [os.path.join(str(tmp_path), "a.py")], [])]
    # Nothing new: no call.
    r.own("send", text="again")
    j._feed(con, "L", "h", store, r.records[-1], None)
    assert len(seen) == 1
    r.call("t2", "Bash", {"command": "cd /x/wt && git push -u origin topic"})
    r.output("t2", "")
    for rec in r.records[-2:]:
        j._feed(con, "L", "h", store, rec, None)
    assert seen[-1] == ("L", "h", [], ["/x/wt"])
    r.call("t3", "Bash", {"command": "cd /x/wt && gh pr merge 5 --rebase"})
    r.output("t3", "Merged pull request #5")
    for rec in r.records[-2:]:
        j._feed(con, "L", "h", store, rec, None)
    assert seen[-1] == ("L", "h", [], ["/x/wt"])
