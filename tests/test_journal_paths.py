import subprocess

import pytest

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
    """Review focus 3: git runs from the nearest directory that still exists."""
    wt = repo / ".claude/worktrees/t"
    (wt / "c.py").write_text("z\n")
    git("add", "c.py", cwd=wt)
    git("commit", "-q", "-m", "add c", cwd=wt)
    h = git("rev-parse", "HEAD", cwd=wt).strip()
    git("worktree", "remove", "--force", str(wt), cwd=repo)
    assert not wt.exists()
    assert commit_files(str(wt), h) == ["c.py"]
