"""Background file indexer with watchdog live updates.

Nothing runs until the first query: ``use()`` walks ``cwd`` in a daemon
thread, then registers a watchdog ``Observer`` to keep the list current
as files are created, deleted, or moved. After ``IDLE_STOP_S`` without a
query the observer stops; the index stays, stale, and the next ``use()``
answers from it at once while a re-walk refreshes it. The observer puts a
watch on every directory under cwd, 100,953 on the Workspace, and its
event processing runs in the daemon's process whether anyone looks or not.

Two layers of ignore rules. A fixed list (``.git``, ``.venv``,
``node_modules``, ``*.pyc``…) holds everywhere, as plain string checks.
Inside a git repo, git decides the rest: the walk asks ``git ls-files``
for what it would track, so every .gitignore, ``info/exclude`` and the
global excludes count exactly as git counts them. Outside any repo git
has no say, and only the fixed list applies.

One extension for a workspace of repos: a directory holding its own
``.git`` directory is its own repo with its own rules, and a gitignored
directory is searched ``SEARCH_DEPTH`` levels deep for such repos. The
Workspace gitignores ``repos/``; read literally that would hide every repo.
A worktree's ``.git`` is a file, so worktrees stay out: otherwise every
file of a repo would be listed once per worktree.

Events cannot shell out to git, so they are judged by the ignore files on
disk, read with pathspec, inside the repos the last walk found. pathspec is
exact for file paths, which is all an event needs; it is not for
directories, which is why the walk leaves those to git. The one gap: git
never reads a .gitignore under an excluded directory, and the event rules
do, so a ``!`` rule there can let a file in until the next walk. Measured
on the Workspace: 2 of 61,839 paths.

Thread safety: ``_paths`` is replaced atomically (single assignment)
after initial walk. Incremental updates append/remove under ``_lock``.
"""

from __future__ import annotations

import bisect
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path

import pathspec
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

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


def _ignore_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


def _ignore_file(path: Path) -> bool:
    if path.name in _IGNORE_NAMES:
        return True
    if path.suffix in _IGNORE_EXTS:
        return True
    n = path.name
    return n.endswith(".min.js") or n.endswith(".min.css") or n.endswith(".map")


#: How many levels into a gitignored directory to look for a nested repo.
#: One finds ``repos/<name>``. Two would also find the thirty
#: ``.playground/<group>/issue-N`` scratch clones on the Workspace, and a
#: scratch directory can hold a whole root filesystem, which searching for
#: repos would walk anyway.
SEARCH_DEPTH = 1

#: (spec, prefilter) for one .gitignore
_Rules = tuple[pathspec.GitIgnoreSpec, re.Pattern[str]]


def _load_rules(path: str) -> _Rules | None:
    """A .gitignore file as (spec, prefilter), or None when absent or empty.

    pathspec tries its patterns one by one in Python: 69µs a path against
    the Workspace's 95. The prefilter joins them into one regex that
    answers "does anything match" in a single C-level pass, and only a hit
    pays for pathspec's exact verdict.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    spec = pathspec.GitIgnoreSpec.from_lines(lines)
    regexes = [
        # ps_d is a named group in every pattern; names cannot repeat.
        p.regex.pattern.replace("(?P<ps_d>", "(?:")
        for p in spec.patterns
        if p.include is not None and getattr(p, "regex", None) is not None
    ]
    if not regexes:
        return None
    return spec, re.compile("|".join(f"(?:{r})" for r in regexes))


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


class FileIndexer:
    """Async file index — starts a background walk + watchdog observer."""

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._mtimes: dict[str, float] = {}  # rel_path -> mtime
        self._cwd: Path | None = None
        self._root = ""  # str(self._cwd), cached for the per-event path ops
        #: the repo roots the last walk found ("" or "rel/dir/")
        self._repos: frozenset[str] = frozenset()
        #: dir ("" or "rel/dir/") -> the ignore rules read there, loaded the
        #: first time an event needs them; emptied by each walk
        self._rules: dict[str, list[_Rules]] = {}
        #: bumped by every walk, so a walk that a newer one superseded
        #: never publishes over it
        self._walks = 0
        self._rewalk_pending = False
        self._observer: Observer | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        #: guards the lifecycle: whether a walk or watcher is live, and when
        #: the index was last asked for
        self._life = threading.Lock()
        self._running = False
        self._last_use = 0.0
        self._halt = threading.Event()

    # --- public API -------------------------------------------------

    #: seconds without a query before the observer stops
    IDLE_STOP_S = 600.0

    def set_root(self, cwd: Path) -> None:
        """Say what to index, without indexing it yet."""
        self._cwd = cwd.resolve()
        self._root = str(self._cwd)

    def start(self, cwd: Path) -> None:
        """Start background walk + watchdog now. Returns immediately."""
        self.set_root(cwd)
        self.use()

    def use(self) -> None:
        """A query is about to read the index: keep it awake, and start it,
        or refresh it after an idle stop, when nothing is watching."""
        if self._cwd is None:
            return
        with self._life:
            self._last_use = time.monotonic()
            if self._running:
                return
            self._running = True
            self._halt.clear()
        first = not self._ready.is_set()
        threading.Thread(target=self._run, args=(first,), daemon=True).start()

    def stop(self) -> None:
        self._halt.set()
        with self._life:
            observer, self._observer = self._observer, None
            self._running = False
        self._stop_observer(observer)

    @staticmethod
    def _stop_observer(observer: Observer | None) -> None:
        if observer is not None:
            observer.stop()
            if observer.is_alive():
                observer.join(timeout=2.0)

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

    def paths_by_mtime(self) -> list[str]:
        """Files sorted by mtime descending (most recently touched first)."""
        with self._lock:
            # Files absent from _mtimes sort last (0.0 fallback).
            return sorted(
                self._paths,
                key=lambda p: self._mtimes.get(p, 0.0),
                reverse=True,
            )

    # --- background walk --------------------------------------------

    #: files walked between publishes — the picker becomes usable on the
    #: first batch instead of waiting out the whole tree
    PUBLISH_EVERY = 2000

    def _publish(
        self, paths: list[str], mtimes: dict[str, float] | None = None
    ) -> None:
        """Make a snapshot of the walk visible to readers, sorted."""
        with self._lock:
            self._paths = sorted(paths)
            if mtimes is not None:
                self._mtimes.update(mtimes)

    def _run(self, first: bool) -> None:
        """Walk, watch, and stop watching once nobody has asked for a while.

        A first walk publishes as it goes. A refresh after an idle stop does
        not: the stale index it replaces is still a good answer.
        """
        self._walk(partial=first)
        with self._life:
            if self._halt.is_set():
                return  # stop() came during the walk
            self._start_observer()
        self._ready.set()  # signal after observer is watching
        interval = min(60.0, self.IDLE_STOP_S / 4)
        while not self._halt.wait(interval):
            with self._life:
                if time.monotonic() - self._last_use < self.IDLE_STOP_S:
                    continue
                observer, self._observer = self._observer, None
                self._running = False
            self._stop_observer(observer)
            return

    def _walk(self, partial: bool = True) -> None:
        """Walk cwd and publish what the rules keep.

        ``partial`` publishes as it goes, so the picker has something on the
        first batch. A re-walk passes False: the index it replaces is still
        good, and a half-built one would make the picker's list shrink.
        """
        self._walks += 1
        mine = self._walks
        walk = _Walk(self._publish if partial else None, self.PUBLISH_EVERY)
        try:
            walk.tree(self._root, "")
        except PermissionError:
            pass
        if mine != self._walks:
            return
        self._repos = frozenset(walk.repos)
        self._rules = {}
        if partial:
            self._publish(walk.paths, walk.mtimes)
        else:
            with self._lock:
                self._paths = sorted(walk.paths)
                self._mtimes = walk.mtimes

    def _rules_changed(self) -> None:
        """A .gitignore or a repo appeared, changed or went away, so what the
        index should hold is no longer what it holds. Rare enough that a
        full re-walk is the simple answer; an editor's save fires several
        events, so they are coalesced first."""
        with self._lock:
            if self._rewalk_pending:
                return
            self._rewalk_pending = True

        def rewalk() -> None:
            time.sleep(0.5)
            with self._lock:
                self._rewalk_pending = False
            self._walk(partial=False)

        threading.Thread(target=rewalk, daemon=True).start()

    def _start_observer(self) -> None:
        cwd = self._cwd
        if cwd is None:
            return
        handler = _IndexHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(cwd), recursive=True)
        self._observer.start()

    # --- incremental updates (called from watchdog thread) ----------

    def _indexable(self, abs_path: str) -> str | None:
        """The index key for ``abs_path``, or None when the ignore rules drop it.

        The fixed list goes first, as string ops with no stat: the observer
        watches every directory under cwd, ignored ones included, and most
        of a busy tree's events land in .git, .venv and __pycache__. Those
        must cost nothing.
        """
        if self._cwd is None or not abs_path.startswith(self._root + "/"):
            return None
        rel = abs_path[len(self._root) + 1 :]
        parts = rel.split("/")
        if any(_ignore_dir(p) for p in parts[:-1]) or _ignore_file(Path(parts[-1])):
            return None
        if self._git_ignored(rel):
            return None
        return rel

    def _git_ignored(self, rel: str) -> bool:
        """Whether the innermost repo the walk found around ``rel`` ignores it,
        by the ignore files from that repo's root down to ``rel``'s
        directory, the deeper overriding the shallower as in git."""
        parts = rel.split("/")
        for i in range(len(parts) - 1, -1, -1):
            root = "/".join(parts[:i]) + "/" if i else ""
            if root in self._repos:
                break
        else:
            return False  # in no repo: git has no say
        verdict = False
        base = root
        for name in [""] + parts[root.count("/") : -1]:
            base += name + "/" if name else ""
            for spec, quick in self._rules_in(base, base == root):
                sub = rel[len(base) :]
                if quick.match(sub) is None:
                    continue
                hit = spec.check_file(sub).include
                if hit is not None:
                    verdict = hit
        return verdict

    def _rules_in(self, base: str, repo_root: bool) -> list[_Rules]:
        """The ignore files in directory ``base``, read once. Read from disk,
        not from the walk's listing: a .gitignore holding ``*`` ignores
        itself, and git never lists it."""
        rules = self._rules.get(base)
        if rules is None:
            full = os.path.join(self._root, base)
            names = [".git/info/exclude", ".gitignore"] if repo_root else [".gitignore"]
            loaded = (_load_rules(os.path.join(full, n)) for n in names)
            rules = [r for r in loaded if r is not None]
            self._rules[base] = rules
        return rules

    def _rules_touched(self, abs_path: str, is_directory: bool) -> bool:
        """Whether an event changed the rules themselves: a .gitignore where
        files are indexed, or a repo's .git directory."""
        name = abs_path.rpartition("/")[2]
        if is_directory:
            if name != ".git" or not abs_path.startswith(self._root + "/"):
                return False
            rel = abs_path[len(self._root) + 1 :]
            return not any(_ignore_dir(p) for p in rel.split("/")[:-1])
        return name == ".gitignore" and self._indexable(abs_path) is not None

    def _add(self, abs_path: str) -> None:
        rel = self._indexable(abs_path)
        if rel is None:
            return
        fp = Path(abs_path)
        if not fp.is_file():
            return
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            mtime = 0.0
        with self._lock:
            # insort, not append+sort: re-sorting the whole list per created
            # file cost 9.6ms at 60k paths, and a build or a git checkout
            # fires thousands of these.
            i = bisect.bisect_left(self._paths, rel)
            if i == len(self._paths) or self._paths[i] != rel:
                self._paths.insert(i, rel)
            self._mtimes[rel] = mtime

    def _remove(self, abs_path: str) -> None:
        # No rules here: git keeps a tracked file even when a pattern matches
        # it, so a path the rules would refuse can still be in the index.
        if self._cwd is None or not abs_path.startswith(self._root + "/"):
            return
        rel = abs_path[len(self._root) + 1 :]
        with self._lock:
            # bisect, not list.remove: a miss scanned all 61k paths, 3.6ms
            # with the GIL held, and every rename (each .pyc write) is one.
            i = bisect.bisect_left(self._paths, rel)
            if i < len(self._paths) and self._paths[i] == rel:
                del self._paths[i]
            self._mtimes.pop(rel, None)


class _Walk:
    """One pass over the tree: the paths it keeps, and the repos it met,
    which events are judged by until the next pass."""

    def __init__(self, publish, every: int) -> None:
        self.publish = publish
        self.every = every
        self.paths: list[str] = []
        self.mtimes: dict[str, float] = {}
        self.repos: set[str] = set()

    def keep(self, rel: str, full: str) -> None:
        try:
            st = os.stat(full)
        except OSError:
            return  # tracked but deleted, or gone mid-walk
        if stat.S_ISDIR(st.st_mode):
            # A submodule: git tracks it as one entry, and its .git is a
            # file, as a worktree's is. It is code the repo ships, so in.
            self.repo(full, rel + "/")
            return
        self.paths.append(rel)
        self.mtimes[rel] = st.st_mtime
        if self.publish is not None and len(self.paths) % self.every == 0:
            self.publish(self.paths, self.mtimes)
            # Let the event loop breathe: this walk competes for the GIL
            # with everything the TUI paints at boot.
            time.sleep(0)

    def tree(self, full: str, rel: str, *, repo_ok: bool = True) -> None:
        """A directory in no repo: the fixed list only, until a repo."""
        if repo_ok and _is_repo(full):
            self.repo(full, rel)
            return
        for root, dirs, files in os.walk(full):
            # str ops rather than Path/relative_to: this runs per file over
            # tens of thousands of them.
            sub = root[len(full) + 1 :]
            base = rel + sub + "/" if sub else rel
            kept = []
            for d in dirs:
                if _ignore_dir(d):
                    continue
                if _is_repo(os.path.join(root, d)):
                    self.repo(os.path.join(root, d), base + d + "/")
                else:
                    kept.append(d)
            dirs[:] = kept
            for f in files:
                if not _ignore_file(Path(f)):
                    self.keep(base + f, os.path.join(root, f))

    def repo(self, full: str, rel: str) -> None:
        """A repo: git lists what it would track, and looks no further."""
        listed = _git_ls(full, "--cached", "--others")
        if listed is None:  # a .git git cannot read: walk it like any tree
            self.tree(full, rel, repo_ok=False)
            return
        self.repos.add(rel)
        for entry in listed:
            if entry.endswith("/"):  # an untracked repo inside this one
                if _is_repo(os.path.join(full, entry)):
                    self.repo(os.path.join(full, entry), rel + entry)
                continue
            parts = entry.split("/")
            if any(_ignore_dir(p) for p in parts[:-1]) or _ignore_file(Path(parts[-1])):
                continue
            self.keep(rel + entry, os.path.join(full, entry))
        for entry in _git_ls(full, "--others", "--ignored", "--directory") or ():
            if entry.endswith("/"):
                self.search(os.path.join(full, entry), rel + entry, 1)

    def search(self, full: str, rel: str, depth: int) -> None:
        """A gitignored directory: nothing here is indexed, but a repo may be."""
        if _is_repo(full):
            self.repo(full, rel)
            return
        try:
            entries = list(os.scandir(full))
        except OSError:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or _ignore_dir(e.name):
                continue
            if _is_repo(e.path):
                self.repo(e.path, rel + e.name + "/")
            elif depth < SEARCH_DEPTH:
                self.search(e.path, rel + e.name + "/", depth + 1)


class _IndexHandler(FileSystemEventHandler):
    def __init__(self, indexer: FileIndexer) -> None:
        self._idx = indexer

    def dispatch(self, event: FileSystemEvent) -> None:
        # Not "modified" for a directory: every git command modifies .git.
        kinds = ("created", "deleted", "moved")
        if event.event_type in kinds or (
            event.event_type == "modified" and not event.is_directory
        ):
            touched = (event.src_path, getattr(event, "dest_path", "") or "")
            if any(
                p and self._idx._rules_touched(p, event.is_directory) for p in touched
            ):
                self._idx._rules_changed()
        super().dispatch(event)

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
