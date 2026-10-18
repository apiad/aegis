"""fs_index: the git-aware listing Alt+O picks from, newest first."""

import os
import subprocess
from pathlib import Path

from aegis.fs_index import FsIndex


def git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def write(path: Path, text: str, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def make_tree(root: Path) -> Path:
    git_init(root)
    write(root / ".gitignore", "repos/\n", 1000)
    write(root / "notes.md", "n", 2000)
    for i, name in enumerate(["alpha", "beta"]):
        repo = root / "repos" / name
        git_init(repo)
        write(repo / ".gitignore", "*.log\n", 1000)
        write(repo / "main.py", "x", 3000 + i)
        write(repo / "debug.log", "noise", 9000)
        write(repo / "node_modules" / "lib.js", "x", 9000)
    write(root / "repos" / "loose.txt", "outside any repo", 9000)
    state = root / ".aegis" / "state"
    write(state / "token", "secret", 9000)
    return state


async def test_lists_nested_repositories_newest_first(tmp_path: Path):
    state = make_tree(tmp_path)
    files = await FsIndex(tmp_path, state).entries()
    paths = [p for p, _ in files]
    assert set(paths) == {
        ".gitignore",
        "notes.md",
        "repos/alpha/.gitignore",
        "repos/alpha/main.py",
        "repos/beta/.gitignore",
        "repos/beta/main.py",
    }
    assert paths[:3] == ["repos/beta/main.py", "repos/alpha/main.py", "notes.md"]
    assert dict(files)["notes.md"] == 2_000_000


async def test_outside_a_repository_only_the_fixed_list_applies(tmp_path: Path):
    write(tmp_path / "a.txt", "a", 1000)
    write(tmp_path / "x.pyc", "a", 1000)
    write(tmp_path / "__pycache__" / "m.py", "a", 1000)
    state = tmp_path / "state"
    write(state / "token", "secret", 1000)
    files = await FsIndex(tmp_path, state).entries()
    assert [p for p, _ in files] == ["a.txt"]


async def test_touched_and_removed_update_the_index(tmp_path: Path):
    write(tmp_path / "a.txt", "a", 1000)
    write(tmp_path / "d" / "b.txt", "b", 1000)
    index = FsIndex(tmp_path, tmp_path / "state")
    await index.entries()
    write(tmp_path / "new.txt", "n", 5000)
    index.touched("new.txt")
    index.removed("d")
    assert [p for p, _ in await index.entries()] == ["new.txt", "a.txt"]


async def test_a_failed_refresh_is_logged_and_the_old_list_answers(
    tmp_path: Path, monkeypatch, caplog
):
    import aegis.fs_index as fs_index

    write(tmp_path / "a.txt", "a", 1000)
    index = FsIndex(tmp_path, tmp_path / "state")
    await index.entries()

    def broken(root, state):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(fs_index, "walk", broken)
    monkeypatch.setattr(fs_index, "FRESH_S", -1.0)
    assert [p for p, _ in await index.entries()] == ["a.txt"]
    await index._walking
    assert "disk on fire" in caplog.text
    assert [p for p, _ in await index.entries()] == ["a.txt"]
