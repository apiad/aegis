"""Background file indexer with watchdog live updates.

Walks ``cwd`` in a daemon thread on ``start()``, then registers a
watchdog ``Observer`` to keep the list current as files are created,
deleted, or moved. Uses its own ignore rules — does not parse
``.gitignore``.

Thread safety: ``_paths`` is replaced atomically (single assignment)
after initial walk. Incremental updates append/remove under ``_lock``.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "__pycache__",
    ".venv", "venv", "env", ".env",
    "node_modules", ".next", ".nuxt",
    "dist", "build", "target", "vendor",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox",
    ".eggs",
    "htmlcov", "coverage",
    ".idea", ".vscode",
    ".aegis", ".claude",
    "__MACOSX",
})

_IGNORE_EXTS: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd",
    ".class",
    ".so", ".dll", ".dylib",
    ".o", ".a", ".obj", ".lib",
    ".exe", ".bin", ".wasm",
    ".gcov",
})

_IGNORE_NAMES: frozenset[str] = frozenset({".DS_Store", ".coverage"})


def _ignore_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


def _ignore_file(path: Path) -> bool:
    if path.name in _IGNORE_NAMES:
        return True
    if path.suffix in _IGNORE_EXTS:
        return True
    n = path.name
    return n.endswith(".min.js") or n.endswith(".min.css") or n.endswith(".map")


class FileIndexer:
    """Async file index — starts a background walk + watchdog observer."""

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._cwd: Path | None = None
        self._observer: Observer | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # --- public API -------------------------------------------------

    def start(self, cwd: Path) -> None:
        """Start background walk + watchdog. Returns immediately."""
        self._cwd = cwd.resolve()
        threading.Thread(target=self._walk, daemon=True).start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            if self._observer.is_alive():
                self._observer.join(timeout=2.0)
            self._observer = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def paths(self) -> list[str]:
        with self._lock:
            return list(self._paths)

    def filter(self, text: str) -> list[str]:
        """Return up to 50 paths containing ``text`` (case-insensitive)."""
        needle = text.lower()
        with self._lock:
            snapshot = self._paths
        return [p for p in snapshot if needle in p.lower()][:50]

    # --- background walk --------------------------------------------

    def _walk(self) -> None:
        cwd = self._cwd
        assert cwd is not None
        paths: list[str] = []
        try:
            for root, dirs, files in os.walk(cwd):
                dirs[:] = [d for d in dirs if not _ignore_dir(d)]
                root_path = Path(root)
                for fname in files:
                    fp = root_path / fname
                    if not _ignore_file(fp):
                        try:
                            paths.append(str(fp.relative_to(cwd)))
                        except ValueError:
                            paths.append(str(fp))
        except PermissionError:
            pass
        paths.sort()
        with self._lock:
            self._paths = paths
        self._start_observer()
        self._ready.set()  # signal after observer is watching

    def _start_observer(self) -> None:
        cwd = self._cwd
        if cwd is None:
            return
        handler = _IndexHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(cwd), recursive=True)
        self._observer.start()

    # --- incremental updates (called from watchdog thread) ----------

    def _add(self, abs_path: str) -> None:
        cwd = self._cwd
        if cwd is None:
            return
        fp = Path(abs_path)
        if not fp.is_file() or _ignore_file(fp):
            return
        try:
            rel = str(fp.relative_to(cwd))
        except ValueError:
            return
        # Skip if any parent component is an ignored dir.
        parts = Path(rel).parts
        if any(_ignore_dir(p) for p in parts[:-1]):
            return
        with self._lock:
            if rel not in self._paths:
                self._paths.append(rel)
                self._paths.sort()

    def _remove(self, abs_path: str) -> None:
        cwd = self._cwd
        if cwd is None:
            return
        try:
            rel = str(Path(abs_path).relative_to(cwd))
        except ValueError:
            return
        with self._lock:
            try:
                self._paths.remove(rel)
            except ValueError:
                pass


class _IndexHandler(FileSystemEventHandler):
    def __init__(self, indexer: FileIndexer) -> None:
        self._idx = indexer

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._add(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._remove(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._remove(event.src_path)
            self._idx._add(event.dest_path)
