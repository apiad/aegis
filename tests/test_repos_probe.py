"""Repo root resolution and the git probe.

The probe runs against a *real* temporary git repo, never a mock. A mocked
probe asserts our model of ``--porcelain=v2``, which is exactly the part
most likely to be wrong, and it would stay green while the real parse broke.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from aegis.repos.probe import (
    Baseline,
    capture_baseline,
    find_repo_root,
    probe_repo,
    read_head_branch,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


def _init(root):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    return root


def _commit(root, name="a.txt", body="hello"):
    (root / name).write_text(body)
    _git(root, "add", name)
    _git(root, "commit", "-qm", f"add {name}")


@pytest.fixture
def repo(tmp_path):
    root = _init(tmp_path / "myrepo")
    _commit(root)
    return root


# --- find_repo_root ---------------------------------------------------

def test_find_root_from_a_file_inside(repo):
    assert find_repo_root(repo / "a.txt") == repo


def test_find_root_from_a_path_that_does_not_exist_yet(repo):
    # A Write creating a new file names a path with no file behind it.
    assert find_repo_root(repo / "src" / "new.py") == repo


def test_find_root_picks_the_nearest_repo_not_the_outer_one(tmp_path):
    outer = _init(tmp_path / "outer")
    _commit(outer)
    inner = _init(outer / "repos" / "inner")
    _commit(inner)
    assert find_repo_root(inner / "a.txt") == inner


def test_find_root_outside_any_repo_is_none(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    assert find_repo_root(loose / "x.txt") is None


def test_find_root_resolves_a_gitdir_pointer_file(tmp_path):
    """A worktree / submodule has ``.git`` as a *file*, not a directory."""
    root = tmp_path / "linked"
    (root / "sub").mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {tmp_path / 'real.git'}\n")
    assert find_repo_root(root / "sub" / "x.py") == root


# --- read_head_branch -------------------------------------------------

def test_read_head_branch_without_a_subprocess(repo):
    assert read_head_branch(repo) == "main"


def test_read_head_branch_follows_a_real_worktree_pointer(tmp_path, repo):
    """A worktree's ``.git`` is a file. Reading ``<root>/.git/HEAD`` blindly
    finds nothing there and reports every worktree as detached."""
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    assert read_head_branch(wt) == "side"
    assert probe_repo(wt).branch == "side"


def test_read_head_branch_is_empty_when_detached(repo):
    sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", sha)
    assert read_head_branch(repo) == ""


# --- probe_repo -------------------------------------------------------

def test_probe_clean_repo(repo):
    st = probe_repo(repo)
    assert st.branch == "main"
    assert st.dirty == 0
    assert st.ahead == 0 and st.behind == 0
    assert st.detached is False
    assert st.op == ""
    assert st.stale is False


def test_probe_counts_modified_and_untracked(repo):
    (repo / "a.txt").write_text("changed")
    (repo / "b.txt").write_text("new")
    assert probe_repo(repo).dirty == 2


def test_probe_counts_commits_ahead_of_upstream(tmp_path, repo):
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    _commit(clone, "c.txt")
    _commit(clone, "d.txt")
    st = probe_repo(clone)
    assert st.ahead == 2
    assert st.behind == 0


def test_probe_counts_commits_behind_upstream(tmp_path, repo):
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    _commit(repo, "e.txt")
    _git(clone, "fetch", "-q")
    st = probe_repo(clone)
    assert st.behind == 1
    assert st.ahead == 0


def test_probe_reports_detached_head(repo):
    sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", sha)
    st = probe_repo(repo)
    assert st.detached is True
    assert st.branch == ""


def test_probe_reports_a_merge_in_progress(repo):
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "a.txt", "side change")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "a.txt", "main change")
    subprocess.run(["git", "merge", "side"], cwd=str(repo),
                   capture_output=True, text=True)  # conflicts on purpose
    assert probe_repo(repo).op == "merge"


def test_probe_of_a_non_repo_is_stale_with_no_branch(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    st = probe_repo(loose)
    assert st.stale is True
    assert st.branch == ""


def test_probe_survives_a_missing_git_binary(repo, monkeypatch):
    monkeypatch.setattr("aegis.repos.probe.shutil.which", lambda _n: None)
    st = probe_repo(repo)
    # Degrades to the free path: branch from .git/HEAD, no counts.
    assert st.branch == "main"
    assert st.dirty == 0
    assert st.stale is True


# --- the session baseline ---------------------------------------------

def test_baseline_records_the_sha_the_session_arrived_at(repo):
    base = capture_baseline(repo)
    assert base.sha == _git(repo, "rev-parse", "HEAD").strip()


def test_baseline_records_the_dirt_that_was_already_there(repo):
    (repo / "a.txt").write_text("hello\nsecond\nthird\n")
    (repo / "loose.txt").write_text("not mine\n")
    base = capture_baseline(repo)
    assert (base.added, base.deleted) == (3, 1)   # a.txt rewritten
    assert base.untracked == frozenset({"loose.txt"})


def test_baseline_of_a_repo_with_no_commits_degrades(tmp_path):
    root = _init(tmp_path / "empty")
    base = capture_baseline(root)
    assert base.sha == ""
    assert (base.added, base.deleted) == (0, 0)


# --- churn since the baseline -----------------------------------------

def test_churn_without_a_baseline_is_zero(repo):
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    st = probe_repo(repo)
    assert (st.added, st.deleted) == (0, 0)


def test_churn_counts_uncommitted_work(repo):
    base = capture_baseline(repo)
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    st = probe_repo(repo, base)
    assert (st.added, st.deleted) == (3, 1)


def test_churn_spans_a_commit(repo):
    """The point of the baseline. ``~n`` goes to zero on every commit; the
    session total must not."""
    base = capture_baseline(repo)
    _commit(repo, "b.txt", "one\ntwo\n")
    (repo / "c.txt").write_text("three\n")
    _git(repo, "add", "c.txt")
    st = probe_repo(repo, base)
    assert st.added == 3          # two committed, one staged
    assert st.dirty == 1          # what the old counter would have shown


def test_churn_discounts_dirt_that_predates_the_session(repo):
    (repo / "a.txt").write_text("theirs\n")      # not the session's doing
    base = capture_baseline(repo)
    (repo / "b.txt").write_text("mine\nalso mine\n")
    _git(repo, "add", "b.txt")
    st = probe_repo(repo, base)
    assert (st.added, st.deleted) == (2, 0)


def test_churn_counts_an_untracked_new_file(repo):
    """``git diff`` cannot see it, and a session of brand-new files is the
    exact case the section exists for."""
    base = capture_baseline(repo)
    (repo / "new.py").write_text("a\nb\nc\nd\n")
    assert probe_repo(repo, base).added == 4


def test_churn_ignores_an_untracked_file_that_predates_the_session(repo):
    (repo / "theirs.txt").write_text("a\nb\nc\n")
    base = capture_baseline(repo)
    assert probe_repo(repo, base).added == 0


def test_churn_ignores_an_ignored_file(repo):
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore logs")
    base = capture_baseline(repo)
    (repo / "big.log").write_text("noise\n" * 100)
    assert probe_repo(repo, base).added == 0


def test_churn_ignores_a_binary_untracked_file(repo):
    base = capture_baseline(repo)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\n" * 10)
    assert probe_repo(repo, base).added == 0


def test_churn_never_goes_negative(repo):
    """An agent that reverts work someone else left uncommitted would drive
    the subtraction below zero, and `-3` lines added is not a fact."""
    (repo / "a.txt").write_text("theirs\nand theirs\n")
    base = capture_baseline(repo)
    (repo / "a.txt").write_text("hello")            # back to the committed
    st = probe_repo(repo, base)
    assert (st.added, st.deleted) == (0, 0)


def test_churn_survives_a_baseline_sha_that_no_longer_exists(repo):
    base = Baseline(sha="0" * 40)
    st = probe_repo(repo, base)
    assert (st.added, st.deleted) == (0, 0)
    assert st.branch == "main"       # the rest of the row still lands
