"""Every file under the config root, newest first: what Alt+O picks from.

Ported from the TUI's indexer (legacy/aegis/tui/file_index.py) without its
watchdog observer, which put a watch on every directory under the root,
100,953 on a large workspace, and ran whether anyone looked. Here a walk
happens only when someone opens the picker: the first ``entries()`` walks, and
one on an index older than ``FRESH_S`` answers from the old list while a
single background walk refreshes it. A write through the Files view updates
the index at once (``touched``, ``removed``).

Two layers of ignore rules. A fixed list (``.git``, ``.venv``,
``node_modules``, ``*.pyc``...) holds everywhere, as plain string checks.
Inside a git repository git decides the rest: the walk asks ``git ls-files``
for what it would track, so every .gitignore, ``info/exclude`` and the global
excludes count exactly as git counts them. Outside any repository only the
fixed list applies.

A directory holding its own ``.git`` directory is its own repository with its
own rules, and a gitignored directory is searched ``SEARCH_DEPTH`` levels deep
for such repositories, because a workspace commonly gitignores ``repos/``. A
worktree's ``.git`` is a file, so worktrees stay out: otherwise every file of
a repository would be listed once per worktree. Nothing under the state root
is ever listed (fs_paths refuses it anyway).
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import subprocess
import time
from pathlib import Path

_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "target",
        "vendor",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "htmlcov",
        "coverage",
        ".idea",
        ".vscode",
        ".aegis",
        ".claude",
        "__MACOSX",
    }
)

_IGNORE_EXTS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".class",
        ".so",
        ".dll",
        ".dylib",
        ".o",
        ".a",
        ".obj",
        ".lib",
        ".exe",
        ".bin",
        ".wasm",
        ".gcov",
    }
)

_IGNORE_NAMES: frozenset[str] = frozenset({".DS_Store", ".coverage"})

log = logging.getLogger(__name__)

#: How many levels into a gitignored directory to look for a nested
#: repository. One finds ``repos/<name>``; two would also walk scratch clones,
#: and a scratch directory can hold a whole root filesystem.
SEARCH_DEPTH = 1

#: An index younger than this answers without a refresh.
FRESH_S = 30.0


def ignore_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


def ignore_file(name: str) -> bool:
    if name in _IGNORE_NAMES or os.path.splitext(name)[1] in _IGNORE_EXTS:
        return True
    return name.endswith((".min.js", ".min.css", ".map"))


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    return any(ignore_dir(p) for p in parts[:-1]) or ignore_file(parts[-1])


def _git_ls(repo: str, *args: str) -> list[str] | None:
    """``git ls-files -z`` in ``repo``, or None when git cannot answer."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "ls-files", "-z", "--exclude-standard", *args],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [e for e in out.stdout.decode("utf-8", "surrogateescape").split("\0") if e]


def _is_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


class _Walk:
    """One pass over the tree: the files it keeps, with their mtimes in ms."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.files: dict[str, int] = {}

    def keep(self, rel: str, full: str) -> None:
        try:
            st = os.stat(full)
        except OSError:
            return  # tracked but deleted, or gone mid-walk
        if stat.S_ISDIR(st.st_mode):
            # A submodule: git lists it as one entry. It is code the
            # repository ships, so in.
            self.repo(full, rel + "/")
            return
        self.files[rel] = st.st_mtime_ns // 1_000_000

    def tree(self, full: str, rel: str, *, repo_ok: bool = True) -> None:
        """A directory in no repository: the fixed list only, until one."""
        if repo_ok and _is_repo(full):
            self.repo(full, rel)
            return
        for root, dirs, files in os.walk(full):
            sub = root[len(full) + 1 :]
            base = rel + sub + "/" if sub else rel
            kept = []
            for d in dirs:
                path = os.path.join(root, d)
                if ignore_dir(d) or path == self.state:
                    continue
                if _is_repo(path):
                    self.repo(path, base + d + "/")
                else:
                    kept.append(d)
            dirs[:] = kept
            for f in files:
                if not ignore_file(f):
                    self.keep(base + f, os.path.join(root, f))

    def repo(self, full: str, rel: str) -> None:
        """A repository: git lists what it would track, and looks no further."""
        if full == self.state or full.startswith(self.state + "/"):
            return
        listed = _git_ls(full, "--cached", "--others")
        if listed is None:  # a .git git cannot read: walk it like any tree
            self.tree(full, rel, repo_ok=False)
            return
        for entry in listed:
            if entry.endswith("/"):  # an untracked repository inside this one
                if _is_repo(os.path.join(full, entry)):
                    self.repo(os.path.join(full, entry).rstrip("/"), rel + entry)
                continue
            if not _ignored(entry):
                self.keep(rel + entry, os.path.join(full, entry))
        for entry in _git_ls(full, "--others", "--ignored", "--directory") or ():
            if entry.endswith("/"):
                self.search(os.path.join(full, entry).rstrip("/"), rel + entry, 1)

    def search(self, full: str, rel: str, depth: int) -> None:
        """A gitignored directory: nothing here is listed, but a repository
        inside may be."""
        if _is_repo(full):
            self.repo(full, rel)
            return
        try:
            entries = list(os.scandir(full))
        except OSError:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or ignore_dir(e.name):
                continue
            if _is_repo(e.path):
                self.repo(e.path, rel + e.name + "/")
            elif depth < SEARCH_DEPTH:
                self.search(e.path, rel + e.name + "/", depth + 1)


def walk(root: Path, state: Path) -> dict[str, int]:
    """Every listed file under ``root``: relative path to mtime in ms."""
    w = _Walk(str(state.resolve()))
    try:
        w.tree(str(root.resolve()), "")
    except PermissionError:
        pass
    return w.files


class FsIndex:
    def __init__(self, root: Path, state: Path) -> None:
        self.root = root
        self.state = state
        self._files: dict[str, int] | None = None
        self._at = 0.0
        self._walking: asyncio.Task | None = None

    async def _walk(self) -> None:
        files = await asyncio.to_thread(walk, self.root, self.state)
        self._files, self._at = files, time.monotonic()

    async def _refresh(self) -> None:
        """A background walk nobody awaits: a failure is logged, and the old
        list keeps answering until the next refresh."""
        try:
            await self._walk()
        except Exception:
            log.exception("the file index's refresh failed; answering from the old one")

    async def entries(self) -> list[tuple[str, int]]:
        """Every listed file with its mtime in ms, newest first."""
        if self._files is None:
            if self._walking is None or self._walking.done():
                self._walking = asyncio.create_task(self._walk())
            await self._walking
        elif time.monotonic() - self._at > FRESH_S and (
            self._walking is None or self._walking.done()
        ):
            self._walking = asyncio.create_task(self._refresh())
        files = self._files or {}
        return sorted(files.items(), key=lambda e: e[1], reverse=True)

    def touched(self, rel: str) -> None:
        """A file was written or created at ``rel``."""
        if self._files is None or _ignored(rel):
            return
        try:
            self._files[rel] = (self.root / rel).stat().st_mtime_ns // 1_000_000
        except OSError:
            self._files.pop(rel, None)

    def removed(self, rel: str) -> None:
        """The file at ``rel`` is gone, or every file under it when a folder."""
        if self._files is None:
            return
        prefix = rel.rstrip("/") + "/"
        for key in [k for k in self._files if k == rel or k.startswith(prefix)]:
            del self._files[key]
