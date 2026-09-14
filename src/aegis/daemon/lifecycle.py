"""Starting a daemon, finding one, and reaping an idle one."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from aegis.config.roots import AegisRoots

log = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_S = 1800.0


def source_mtime() -> float:
    """The newest mtime under the installed aegis package.

    Exact for an editable install, which is how aegis is developed: every
    edit moves it. Silent for a wheel, where nothing under the package
    changes after install, so the staleness check below simply never fires
    there.
    """
    root = Path(__file__).resolve().parent.parent
    newest = 0.0
    for p in root.rglob("*.py"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def is_stale(rec) -> bool:
    """Whether ``rec``'s daemon is running code that has since been edited.

    The registry records a ``version`` and comparing it would not answer
    this: a dev edit does not bump a release number, so the daemon that
    served Alex stale code all morning matched his working tree at 0.37.0
    on both sides. What separates them is that the process is older than
    the source.
    """
    try:
        return source_mtime() > rec.started
    except Exception:  # noqa: BLE001 — never block an attach on this
        return False


def nothing_to_lose(roots) -> bool:
    """Whether replacing this root's daemon would cost the user nothing.

    Read from the workspace snapshot rather than asked of the daemon,
    which would need a protocol message for one question. It is the
    persisted roster, so it can lag what the daemon holds right now, and
    the lag is handled by which way the answer errs: anything open, or any
    doubt at all, means do not replace. The cost of being wrong that way is
    a warning the user can act on. The cost of being wrong the other way is
    their work.
    """
    from aegis.state.workspace import load

    try:
        ws = load(roots.state_dir)
    except Exception:  # noqa: BLE001 — includes CorruptWorkspace
        return False
    if ws is None:
        return True
    return not (ws.tabs or ws.terminals or ws.files)


class SpawnFailed(Exception):
    """An autostarted daemon never came up."""


def socket_path(roots: AegisRoots) -> Path:
    return roots.state_dir / "daemon.sock"


class DaemonAlreadyRunning(Exception):
    """`aegis serve` found another daemon holding this root's lock."""


def lock_path(roots: AegisRoots) -> Path:
    return roots.state_dir / "daemon.lock"


class DaemonLock:
    """One root's daemon lock, held for as long as the daemon runs.

    An flock rather than a pid file: taking it is atomic, which a
    probe-then-spawn is not, and the kernel drops it when the holder dies,
    SIGKILL included, so a crashed daemon never locks the next one out.
    Python opens file descriptors non-inheritable, so the harness processes
    a daemon spawns do not keep the lock after the daemon is gone.
    """

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def acquire_daemon_lock(roots: AegisRoots, *,
                        wait_s: float = 0.0) -> DaemonLock | None:
    """Take this root's daemon lock, or None when another process holds it.

    ``wait_s`` is for the daemon, which retries briefly: a client's
    `daemon_lock_held` probe holds the lock for an instant, and a daemon
    that tried in that instant and gave up would leave no daemon at all. A
    real rival holds the lock for its whole life, so a short window never
    lets two through.
    """
    path = lock_path(roots)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return DaemonLock(fd)
        except BlockingIOError:
            os.close(fd)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def daemon_lock_held(roots: AegisRoots) -> bool:
    """Whether a daemon for this root is running or booting, by its lock.

    A root that never had a daemon has no lock file, and probing must not
    create one: the client may still refuse to spawn over a broken config.
    """
    if not lock_path(roots).exists():
        return False
    lock = acquire_daemon_lock(roots)
    if lock is None:
        return True
    lock.release()
    return False


def idle_timeout_s() -> float:
    """Seconds of contiguous idleness before a daemon reaps itself.

    An environment variable rather than a ``.aegis.yaml`` block: reaping is
    a property of the host's habits -- a laptop that should self-clean
    versus a server that should not -- and ``.aegis.yaml`` is per-project
    and committed, so a laptop policy would follow the repo onto the VPS.
    """
    raw = os.environ.get("AEGIS_IDLE_TIMEOUT")
    if raw is None:
        return DEFAULT_IDLE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_S
    return max(0.0, value)


class IdleReaper:
    """Sets ``stop`` after ``timeout_s`` of zero views AND zero sessions.

    Both conditions, deliberately. Zero views alone would reap the VPS
    daemon every night -- it runs agents nobody is watching, which is the
    entire point of it. Zero sessions alone would never fire on a laptop
    with a stale tab open.

    Idleness is a contiguous run, not a total: any view or session resets
    the clock, so a daemon touched every 20 minutes all day is never reaped
    mid-use.
    """

    def __init__(self, registry, manager, *, timeout_s: float,
                 stop: asyncio.Event, interval_s: float = 5.0) -> None:
        self._registry = registry
        self._manager = manager
        self._timeout = timeout_s
        self._stop = stop
        self._interval = interval_s

    def _idle(self) -> bool:
        if self._registry.list():
            return False
        try:
            return not self._manager.list_sessions()
        except Exception:  # noqa: BLE001
            return False   # cannot tell => not idle; never reap on a guess

    async def run(self) -> None:
        if self._timeout <= 0:
            return
        idle_for = 0.0
        while not self._stop.is_set():
            await asyncio.sleep(self._interval)
            if self._idle():
                idle_for += self._interval
                if idle_for >= self._timeout:
                    log.info("daemon idle for %.0fs; exiting", idle_for)
                    self._stop.set()
                    return
            else:
                idle_for = 0.0


def _spawn_detached(root: Path) -> None:
    """Fork `aegis serve` into its own session, detached from this tty.

    ``start_new_session`` is what makes it survive the terminal that
    started it: without it the daemon is in the attaching shell's process
    group and dies with the terminal, which is the one thing a daemon may
    not do.
    """
    # `--autostarted` is what lets the daemon record that a client started
    # it, which is the only condition under which a client may later stop
    # it. A daemon a person or systemd starts carries no such mark and is
    # therefore unstoppable from any TUI.
    subprocess.Popen(
        [sys.executable, "-m", "aegis", "serve", "--cwd", str(root),
         "--autostarted"],
        cwd=str(root), start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _connectable(path: Path) -> bool:
    try:
        _reader, writer = await asyncio.open_unix_connection(str(path))
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def ensure_daemon(root: Path, *, timeout_s: float = 20.0,
                        preflight=None) -> Path:
    """Return a connectable socket for ``root``, starting one if needed.

    Liveness is "the socket accepts a connection", not "the file exists":
    a SIGKILLed daemon leaves the file behind, and a stale file that
    satisfied this check would make every `aegis` invocation hang against
    a dead socket.

    ``preflight`` runs immediately before a spawn and may raise to abort
    it. It exists for the config check: a daemon dies silently on a broken
    ``.aegis.yaml`` (its stderr is /dev/null), so without one the user
    waits out the whole timeout to be told the daemon did not come up
    rather than being shown the parse error. It runs ONLY on the spawn
    path -- when a daemon is already live its config is whatever it
    booted with, and refusing to attach over a local parse error would
    lock the user out of a working brain.
    """
    roots = AegisRoots.for_project(Path(root))
    path = socket_path(roots)
    if await _connectable(path):
        return path

    # Spawn only while nobody holds the lock. A holder is a daemon that is
    # booting, or one still exiting (a replaced stale daemon, a kill):
    # spawning beside it produces a process that finds the lock taken and
    # exits, which is why this keeps asking until it has spawned once.
    spawned = False
    waited = 0.0
    step = 0.05
    while True:
        if not spawned and not daemon_lock_held(roots):
            if preflight is not None:
                preflight()
            _spawn_detached(Path(root))
            spawned = True
        if waited >= timeout_s:
            break
        await asyncio.sleep(step)
        waited += step
        if await _connectable(path):
            return path
    raise SpawnFailed(
        f"daemon for {root} did not come up within {timeout_s:.0f}s; "
        f"try `aegis serve --cwd {root}` in a terminal to see why")
