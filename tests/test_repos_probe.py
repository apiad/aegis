"""The git probe and the gh lookup on real temp repos with a bare remote."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from aegis.repos import gh, probe

G = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def run(cwd, *args):
    return subprocess.run(
        [*G, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(cwd: Path, name: str, text="y\n") -> str:
    (cwd / name).write_text(text)
    run(cwd, "add", name)
    run(cwd, "commit", "-q", "-m", f"add {name}")
    return run(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    r = tmp_path / "repo"
    r.mkdir()
    run(r, "init", "-q", "-b", "main")
    run(r, "remote", "add", "origin", str(remote))
    commit(r, "a.py", "x\n")
    run(r, "push", "-q", "-u", "origin", "main")
    run(r, "remote", "set-head", "origin", "main")
    return r


def test_dirty_untracked_and_branch(repo):
    (repo / "a.py").write_text("x\nmore\n")
    (repo / "new.txt").write_text("n\n")
    p = probe.probe(str(repo))
    assert (p.branch, p.upstream, p.detached, p.op) == (
        "main",
        "origin/main",
        False,
        "",
    )
    assert p.dirty == {"a.py": "+1 −0", "new.txt": "new"}


def test_a_worktree_has_its_own_branch_and_dirt(repo, tmp_path):
    wt = tmp_path / "wt"
    run(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
    (wt / "b.py").write_text("b\n")
    p = probe.probe(str(wt))
    assert (p.branch, p.upstream) == ("topic", "")
    assert p.dirty == {"b.py": "new"}
    assert probe.probe(str(repo)).dirty == {}


def test_a_removed_directory_is_none(tmp_path):
    assert probe.probe(str(tmp_path / "gone")) is None


def test_a_merge_in_progress_is_reported(repo):
    run(repo, "checkout", "-q", "-b", "side")
    commit(repo, "a.py", "side\n")
    run(repo, "checkout", "-q", "main")
    commit(repo, "a.py", "main\n")
    subprocess.run([*G, "merge", "side"], cwd=repo, capture_output=True)
    assert probe.probe(str(repo)).op == "merge"


def test_unpushed_until_pushed(repo):
    h = commit(repo, "c.py")
    up = probe.base(str(repo), "origin/main")
    assert probe.unpushed(str(repo), [h], up) == [h]
    run(repo, "push", "-q")
    assert probe.unpushed(str(repo), [h], up) == []


def test_a_rebase_merged_and_deleted_branch_counts_as_pushed(repo, tmp_path):
    wt = tmp_path / "wt"
    run(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
    mine = commit(wt, "t.py")
    run(wt, "push", "-q", "-u", "origin", "topic")
    # The PR merges by rebase on the remote: main moves on first, so topic's
    # commit lands there under another sha; then the branch is deleted.
    commit(repo, "other.py")
    run(repo, "push", "-q")
    other = tmp_path / "other"
    run(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other))
    run(other, "cherry-pick", mine)
    run(other, "push", "-q", "origin", "main")
    run(other, "push", "-q", "origin", "--delete", "topic")
    run(wt, "fetch", "-q", "--prune")
    p = probe.probe(str(wt))
    up = probe.base(str(wt), p.upstream)
    assert up == "origin/main"
    assert probe.unpushed(str(wt), [mine], up) == []


def test_a_sha_the_repo_lost_is_dropped(repo):
    h = commit(repo, "c.py")
    run(repo, "commit", "-q", "--amend", "-m", "amended")
    run(repo, "reflog", "expire", "--expire=now", "--all")
    run(repo, "gc", "-q", "--prune=now")
    up = probe.base(str(repo), "origin/main")
    now = run(repo, "rev-parse", "HEAD")
    assert probe.unpushed(str(repo), [h, now], up) == [now]


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    out = tmp_path / "gh.json"
    calls = tmp_path / "calls"
    script = bin_ / "gh"
    script.write_text(f"#!/bin/sh\necho x >> {calls}\ncat {out}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    gh._cache.clear()
    return out, calls


def test_gh_prs_parse_and_cache(fake_gh, tmp_path):
    out, calls = fake_gh
    out.write_text(
        json.dumps(
            [
                {
                    "number": 7,
                    "url": "https://github.com/o/r/pull/7",
                    "title": "t",
                    "state": "OPEN",
                    "statusCheckRollup": [
                        {"conclusion": "SUCCESS"},
                        {"status": "IN_PROGRESS"},
                    ],
                },
            ]
        )
    )
    want = [
        {
            "n": 7,
            "url": "https://github.com/o/r/pull/7",
            "title": "t",
            "state": "open",
            "ci": "CI running",
        }
    ]
    assert gh.prs(str(tmp_path), "topic") == want
    assert gh.prs(str(tmp_path), "topic") == want
    assert calls.read_text().count("x") == 1
    gh.forget(str(tmp_path))
    gh.prs(str(tmp_path), "topic")
    assert calls.read_text().count("x") == 2


def test_gh_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    gh._cache.clear()
    assert gh.prs(str(tmp_path), "topic") is None
