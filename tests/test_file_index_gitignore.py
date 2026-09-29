"""The file index honours .gitignore, with a nested repo as its own scope.

The index had its own ignore list and nothing else, so a scratch root
filesystem an agent built under the Workspace's gitignored `.playground/`
went into it file by file, and its deletion was the burst behind #18. The
Workspace also gitignores `repos/`, so a literal reading of .gitignore would
hide every repo: a directory holding its own `.git` directory starts a fresh
scope, and an ignored directory is searched a bounded depth for one.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from aegis.tui.file_index import FileIndexer


def _files(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def _repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _indexed(root: Path) -> list[str]:
    idx = FileIndexer()
    idx.start(root)
    try:
        assert idx._ready.wait(10)
        return idx.paths
    finally:
        idx.stop()


def _wait(pred, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_root_gitignore_drops_files_and_directories(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\nout/\n")
    _files(tmp_path, "src/app.py", "src/debug.log", "out/bundle.js")
    assert _indexed(tmp_path) == [".gitignore", "src/app.py"]


def test_negations_follow_git(tmp_path):
    _repo(tmp_path)
    # The Workspace's own shape: only the markdown under Sources is kept.
    (tmp_path / ".gitignore").write_text(
        "vault/Sources/**\n!vault/Sources/**/\n!vault/Sources/**/*.md\n"
    )
    _files(tmp_path, "vault/Sources/a/page.md", "vault/Sources/a/raw.json")
    assert "vault/Sources/a/page.md" in _indexed(tmp_path)
    assert "vault/Sources/a/raw.json" not in _indexed(tmp_path)


def test_a_repo_inside_an_ignored_directory_is_indexed_by_its_own_rules(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("repos/\n")
    _repo(tmp_path / "repos" / "r1")
    (tmp_path / "repos" / "r1" / ".gitignore").write_text("secret.txt\n")
    _files(
        tmp_path,
        "notes.md",
        "repos/loose.txt",  # in the ignored dir, in no repo
        "repos/r1/src/a.py",
        "repos/r1/secret.txt",
    )
    assert _indexed(tmp_path) == [
        ".gitignore",
        "notes.md",
        "repos/r1/.gitignore",
        "repos/r1/src/a.py",
    ]


def test_a_worktree_is_not_a_repo_to_index(tmp_path):
    """A worktree's `.git` is a file. Indexing worktrees would list every
    file of a repo once per worktree."""
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".playground/\n")
    (tmp_path / ".playground" / "wt-1").mkdir(parents=True)
    (tmp_path / ".playground" / "wt-1" / ".git").write_text("gitdir: /x\n")
    _files(tmp_path, ".playground/wt-1/a.py")
    assert _indexed(tmp_path) == [".gitignore"]


def test_the_search_for_repos_in_an_ignored_directory_is_bounded(tmp_path):
    """Only a repo directly inside the ignored directory counts, as
    `repos/<name>` does. A scratch directory can hold a whole root
    filesystem, or thirty throwaway clones one level further down."""
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".playground/\n")
    _repo(tmp_path / ".playground" / "near")
    _repo(tmp_path / ".playground" / "group" / "clone")
    _files(tmp_path, ".playground/near/n.py", ".playground/group/clone/c.py")
    assert _indexed(tmp_path) == [".gitignore", ".playground/near/n.py"]


def test_events_follow_the_same_rules(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\nrepos/\n")
    _repo(tmp_path / "repos" / "r1")
    _files(tmp_path, "keep.py")
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert idx._ready.wait(10)
        _files(tmp_path, "trace.log", "repos/stray.py", "repos/r1/new.py")
        assert _wait(lambda: "repos/r1/new.py" in idx.paths)
        assert "trace.log" not in idx.paths
        assert "repos/stray.py" not in idx.paths
    finally:
        idx.stop()


def test_editing_a_gitignore_re_indexes(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("")
    _files(tmp_path, "a.py", "b.tmp")
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert idx._ready.wait(10)
        assert "b.tmp" in idx.paths
        (tmp_path / ".gitignore").write_text("*.tmp\n")
        assert _wait(lambda: "b.tmp" not in idx.paths)
        assert "a.py" in idx.paths
    finally:
        idx.stop()


def test_outside_a_repo_gitignore_has_no_say(tmp_path):
    """git reads no .gitignore outside a repo, and neither does the index."""
    (tmp_path / ".gitignore").write_text("*.log\n")
    _files(tmp_path, "a.py", "b.log")
    assert _indexed(tmp_path) == [".gitignore", "a.py", "b.log"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "protocol.file.allow=always", *args],
        check=True, capture_output=True,
    )


def test_a_submodule_is_indexed(tmp_path):
    """A submodule's .git is a file, as a worktree's is, but git tracks it:
    it is code the repo ships."""
    lib = tmp_path / "lib-src"
    _repo(lib)
    _files(lib, "core.py")
    _git(lib, "add", ".")
    _git(lib, "commit", "-qm", "init")
    app = tmp_path / "app"
    _repo(app)
    _git(app, "submodule", "add", "-q", str(lib), "deps/lib")
    assert "deps/lib/core.py" in _indexed(app)


def test_a_gitignore_that_ignores_itself_still_counts_for_events(tmp_path):
    """`*` in a .gitignore ignores the file too, so git never lists it; the
    event rules read it from disk."""
    _repo(tmp_path)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / ".gitignore").write_text("*\n")
    _files(tmp_path, "keep.py")
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert idx._ready.wait(10)
        assert idx.paths == ["keep.py"]
        _files(tmp_path, "scratch/report.md", "fresh.py")
        assert _wait(lambda: "fresh.py" in idx.paths)
        assert "scratch/report.md" not in idx.paths
    finally:
        idx.stop()


def test_a_tracked_file_matching_a_pattern_is_kept_and_can_be_removed(tmp_path):
    """git keeps a tracked file even when a pattern matches it. So does the
    index, and deleting it must still take it out."""
    _repo(tmp_path)
    _files(tmp_path, "notebook.ipynb")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / ".gitignore").write_text("*.ipynb\n")
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert idx._ready.wait(10)
        assert "notebook.ipynb" in idx.paths
        (tmp_path / "notebook.ipynb").unlink()
        assert _wait(lambda: "notebook.ipynb" not in idx.paths)
    finally:
        idx.stop()
