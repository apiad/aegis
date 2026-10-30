import os
import subprocess

import pytest

from aegis.journal import paths
from aegis.journal.paths import commit_files, full, name

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(*args, cwd):
    return subprocess.run(
        [*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "repo"
    r.mkdir()
    git("init", "-q", "-b", "main", cwd=r)
    (r / "a.py").write_text("x\n")
    git("add", "a.py", cwd=r)
    git("commit", "-q", "-m", "first", cwd=r)
    git("worktree", "add", "-q", "-b", "topic", str(r / ".claude/worktrees/t"), cwd=r)
    return r


def test_a_worktree_path_is_named_by_the_main_checkout(repo):
    wt = repo / ".claude/worktrees/t"
    assert name(str(wt / "src/b.py")) == (str(repo), "src/b.py")
    assert name(str(repo / "a.py")) == (str(repo), "a.py")
    assert full(str(repo), "src/b.py") == str(repo / "src/b.py")


def test_a_path_outside_any_repo_is_its_absolute_path(tmp_path):
    p = tmp_path.resolve() / "x" / "y.txt"
    assert name(str(p)) == (None, str(p))
    assert full(None, str(p)) == str(p)


def test_a_commit_lists_its_files_from_either_checkout(repo):
    wt = repo / ".claude/worktrees/t"
    (wt / "b.py").write_text("y\n")
    git("add", "b.py", cwd=wt)
    git("commit", "-q", "-m", "add b", cwd=wt)
    h = git("rev-parse", "HEAD", cwd=wt).strip()
    assert commit_files(str(wt), h) == ["b.py"]
    assert commit_files(str(repo), h) == ["b.py"]
    assert commit_files(str(repo), "0" * 40) is None


def test_a_removed_worktree_still_lists_its_commit(repo):
    """git runs from the nearest directory that still exists."""
    wt = repo / ".claude/worktrees/t"
    (wt / "c.py").write_text("z\n")
    git("add", "c.py", cwd=wt)
    git("commit", "-q", "-m", "add c", cwd=wt)
    h = git("rev-parse", "HEAD", cwd=wt).strip()
    git("worktree", "remove", "--force", str(wt), cwd=repo)
    assert not wt.exists()
    assert commit_files(str(wt), h) == ["c.py"]


def test_commit_files_handles_non_ascii_names(repo):
    wt = repo / ".claude/worktrees/t"
    (wt / "café.py").write_text("x\n")
    git("add", "café.py", cwd=wt)
    git("commit", "-q", "-m", "add café", cwd=wt)
    h = git("rev-parse", "HEAD", cwd=wt).strip()
    assert commit_files(str(wt), h) == ["café.py"]


def test_name_does_not_raise_on_nul_byte():
    result = name("a\0b")
    assert result == (None, "a\0b")


def test_a_blob_hash_is_not_a_commit_with_files(repo):
    blob = git("rev-parse", "HEAD:a.py", cwd=repo).strip()
    assert commit_files(str(repo), blob) is None
    assert commit_files(str(repo), blob[:7]) is None


def git_repo(path, origin):
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("remote", "add", "origin", origin, cwd=path)
    return path.resolve()


def test_checkout_of_finds_the_top_whose_origin_is_the_slug(tmp_path):
    a = git_repo(tmp_path / "atlas", origin="git@github.com:acme-co/atlas.git")
    b = git_repo(tmp_path / "notes", origin="https://github.com/ana/notes")
    assert paths.remote_slug(str(b)) == "ana/notes"
    assert paths.checkout_of("acme-co/atlas", [str(b), str(a)], None) == str(a)
    assert paths.checkout_of("Acme-Co/Atlas", [str(a)], None) == str(a)
    assert paths.checkout_of("acme-co/ledger", [str(a)], None) is None
    assert paths.remote_slug(str(tmp_path / "gone")) is None


def test_a_commit_is_found_by_its_exact_subject_up_to_a_time(repo):
    def commit(subject, when):
        env = {**os.environ, "GIT_COMMITTER_DATE": f"{when} +0000"}
        subprocess.run(
            [*GIT, "commit", "-q", "--allow-empty", "-m", subject],
            cwd=repo,
            check=True,
            env=env,
        )
        return git("rev-parse", "HEAD", cwd=repo).strip()

    old = commit("fix: y", 1700001000)
    new = commit("fix: y", 1700002000)
    commit("fix: y and more", 1700002500)
    assert paths.commit_by_subject(str(repo), "fix: y", 1700003000) == new
    assert paths.commit_by_subject(str(repo), "fix: y", 1700001500) == old
    assert paths.commit_by_subject(str(repo), "fix: *", 1700003000) is None
    assert paths.commit_by_subject(str(repo), "gone", 1700003000) is None
