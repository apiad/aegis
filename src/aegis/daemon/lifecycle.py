"""Starting a daemon, finding one, and reaping an idle one."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from aegis.config.roots import AegisRoots

log = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_S = 1800.0


class SpawnFailed(Exception):
    """An autostarted daemon never came up."""


def socket_path(roots: AegisRoots) -> Path:
    return roots.state_dir / "daemon.sock"


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
    subprocess.Popen(
        [sys.executable, "-m", "aegis", "serve", "--cwd", str(root)],
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


async def ensure_daemon(root: Path, *, timeout_s: float = 20.0) -> Path:
    """Return a connectable socket for ``root``, starting one if needed.

    Liveness is "the socket accepts a connection", not "the file exists":
    a SIGKILLed daemon leaves the file behind, and a stale file that
    satisfied this check would make every `aegis` invocation hang against
    a dead socket.
    """
    roots = AegisRoots.for_project(Path(root))
    path = socket_path(roots)
    if await _connectable(path):
        return path

    _spawn_detached(Path(root))
    waited = 0.0
    step = 0.05
    while waited < timeout_s:
        await asyncio.sleep(step)
        waited += step
        if await _connectable(path):
            return path
    raise SpawnFailed(
        f"daemon for {root} did not come up within {timeout_s:.0f}s; "
        f"try `aegis serve --cwd {root}` in a terminal to see why")
