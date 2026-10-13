"""One cgroup per session, when the server's unit delegates its cgroup (#360).

Under systemd with ``Delegate=yes`` the server owns its unit's cgroup and may
build a tree below it. It then runs every harness child, and all that child
starts, in a cgroup of its own with a memory cap, so one session's runaway
test run hits a memcg OOM inside that session and nothing else::

    <unit>/                 the unit: its MemoryMax still covers everything
    <unit>/server/          the server, and whatever the unit started it through
    <unit>/sessions/<id>/   one harness child and its descendants

The server leaves the unit's own cgroup first because of cgroup v2's
no-internal-processes rule: a cgroup that enables controllers for its
children may hold no processes itself. It moves every process of the unit
into ``server/``, which is only itself and its ancestors (``uv run``, a
shell), then enables ``memory`` and ``pids`` below the unit and below
``sessions/``. Stopping the unit still kills every session: the session
cgroups sit inside the unit, and ``KillMode=control-group`` kills the subtree.

Delegation is detected, never assumed. The server must be on cgroup v2, in a
``*.service`` cgroup it can write, that holds nothing but this process and its
ancestors. Anything else (no delegation, a terminal's ``.scope``, a test run
inside a session's own cgroup) keeps today's behaviour: every child in the
server's cgroup, one log line saying why. The service check is what stops a
server started inside another session's cgroup, as the test suite is, from
carving that session up, and a server started from a terminal from moving
the terminal's shell.

The child joins its cgroup before it execs, through ``/bin/sh``: the shell
writes its own pid to ``cgroup.procs`` and execs the harness, so the harness
keeps the pid the server knows, and nothing it starts runs outside the cap.
Not a ``preexec_fn``, which is unsafe in a process with threads. If the join
fails the harness still starts, uncontained, and says so on its stderr: a
session that cannot start is worse than one without a cap.

The limits: ``memory.max`` is ``session_memory_max`` from ``.aegis.yaml``
(default 40% of the host's RAM), ``memory.high`` a tenth below it, so the
kernel reclaims and throttles before it kills. ``memory.oom.group`` is 0: an
OOM inside a session kills its largest process, which is nearly always a
runaway test worker, not the whole session. With 1 the agent itself would die
with its tests, and the person would lose the turn that could have noticed.

A session's cgroup is emptied (``cgroup.kill``) and removed when its process
ends, by exit, Stop, Close or shutdown, so a background job the harness left
does not outlive the session. Boot removes the empty ``sessions/*`` a crash
left behind.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("aegis.cgroups")

MOUNT = Path("/sys/fs/cgroup")
PROC = Path("/proc")
CONTROLLERS = ("memory", "pids")
DEFAULT_MEMORY_MAX = "40%"
# memory.high as a share of memory.max.
HIGH_SHARE = 0.9
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT%]?)\s*$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
# The join, then the harness: $0 is the cgroup, "$@" the harness's argv.
_JOIN = (
    'echo $$ > "$0/cgroup.procs" '
    '|| echo "aegis: could not join $0; running without its memory cap" >&2; '
    'exec "$@"'
)
RETRY_RMDIR_S = 0.2
RMDIR_TRIES = 10


def parse_size(value: object, total: int) -> int | None:
    """Bytes for a ``session_memory_max``: an integer of bytes, a size with
    K, M, G or T (powers of 1024, as systemd reads ``MemoryMax``), a share of
    ``total`` like ``40%``, or ``max`` for no cap (None). ValueError when it
    is none of these."""
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a size")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"{value!r} is not a positive size")
        return value
    if isinstance(value, str) and value.strip().lower() == "max":
        return None
    m = _SIZE.match(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"{value!r} is not a size like 3G, 40% or max")
    n, unit = float(m[1]), m[2].upper()
    if unit == "%":
        if not 0 < n <= 100:
            raise ValueError(f"{value!r} is not a share between 0 and 100%")
        return int(total * n / 100)
    if n <= 0:
        raise ValueError(f"{value!r} is not a positive size")
    return int(n * _UNITS[unit])


def total_ram() -> int:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def own_cgroup(text: str) -> str | None:
    """The cgroup v2 path in ``/proc/<pid>/cgroup``, or None on v1 or hybrid,
    where memory is not on the v2 hierarchy."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1 or not lines[0].startswith("0::"):
        return None
    return lines[0][3:]


def _ppid(proc: Path, pid: int) -> int | None:
    try:
        return int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def _ancestry(proc: Path, pid: int) -> set[int]:
    """``pid`` and every process above it."""
    out = set()
    p: int | None = pid
    while p and p not in out:
        out.add(p)
        p = _ppid(proc, p)
    return out


def _procs(d: Path) -> list[int]:
    try:
        return [int(x) for x in (d / "cgroup.procs").read_text().split()]
    except (OSError, ValueError):
        return []


def wrap(cgroup: Path | None, argv: list[str]) -> list[str]:
    """``argv`` started inside ``cgroup``, or unchanged when there is none.
    Raises FileNotFoundError for a missing binary, as the spawn would without
    the shell in front."""
    if cgroup is None:
        return argv
    import shutil

    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(argv[0])
    return ["/bin/sh", "-c", _JOIN, str(cgroup), *argv]


class SessionCgroups:
    def __init__(self, unit: Path, memory_max: Callable[[], object]) -> None:
        self.unit = unit
        self.sessions = unit / "sessions"
        self._memory_max = memory_max

    @staticmethod
    def check(
        mount: Path = MOUNT, proc: Path = PROC, pid: int | None = None
    ) -> str | Path:
        """The unit's cgroup when this server may build below it, else why not.
        Reads only."""
        pid = os.getpid() if pid is None else pid
        try:
            text = (proc / str(pid) / "cgroup").read_text()
        except OSError as e:
            return f"cannot read the server's cgroup: {e}"
        rel = own_cgroup(text)
        if rel is None:
            return "not on cgroup v2"
        unit = mount / rel.lstrip("/")
        if not unit.name.endswith(".service"):
            return f"{rel} is not a service's cgroup"
        try:
            available = (unit / "cgroup.controllers").read_text().split()
        except OSError as e:
            return f"cannot read {unit}/cgroup.controllers: {e}"
        if "memory" not in available:
            return f"the memory controller is not available in {rel}"
        for f in (unit, unit / "cgroup.procs", unit / "cgroup.subtree_control"):
            if not os.access(f, os.W_OK):
                return f"{f} is not writable; the unit needs Delegate=yes"
        mine = _ancestry(proc, pid)
        if others := [p for p in _procs(unit) if p not in mine]:
            return f"{rel} holds processes other than the server: {others[:5]}"
        return unit

    @classmethod
    def setup(
        cls,
        memory_max: Callable[[], object],
        mount: Path = MOUNT,
        proc: Path = PROC,
        pid: int | None = None,
    ) -> SessionCgroups | None:
        """Leave the unit's cgroup for ``server/`` and enable the controllers,
        or None, logged, when the unit is not delegated."""
        if sys.platform != "linux":
            return None
        found = cls.check(mount, proc, pid)
        if isinstance(found, str):
            # A server under systemd should have had it; one in a terminal not.
            level = logging.WARNING if os.environ.get("INVOCATION_ID") else logging.INFO
            log.log(level, "aegis: no cgroup per session: %s", found)
            return None
        unit = found
        self = cls(unit, memory_max)
        try:
            available = (unit / "cgroup.controllers").read_text().split()
            enable = " ".join(f"+{c}" for c in CONTROLLERS if c in available)
            (unit / "server").mkdir(exist_ok=True)
            for p in _procs(unit):
                (unit / "server" / "cgroup.procs").write_text(f"{p}\n")
            (unit / "cgroup.subtree_control").write_text(enable)
            self.sessions.mkdir(exist_ok=True)
            (self.sessions / "cgroup.subtree_control").write_text(enable)
        except OSError as e:
            log.warning("aegis: no cgroup per session: setting up %s: %s", unit, e)
            return None
        self.sweep()
        log.info("aegis: one cgroup per session under %s", self.sessions)
        return self

    def sweep(self) -> None:
        """Remove the session cgroups a crash left with nothing in them."""
        for d in self.sessions.iterdir() if self.sessions.is_dir() else ():
            if d.is_dir() and not _procs(d):
                self._rmdir(d, 1)

    def path(self, log_id: str) -> Path:
        return self.sessions / log_id

    def create(self, log_id: str) -> Path | None:
        """The session's cgroup with its limits written, or None, logged, when
        it cannot be made, and the session runs uncontained."""
        d = self.path(log_id)
        try:
            limit = parse_size(self._memory_max(), total_ram())
        except ValueError:
            limit = parse_size(DEFAULT_MEMORY_MAX, total_ram())
        try:
            d.mkdir(exist_ok=True)
            high = "max" if limit is None else str(int(limit * HIGH_SHARE))
            (d / "memory.max").write_text("max" if limit is None else str(limit))
            (d / "memory.high").write_text(high)
            (d / "memory.oom.group").write_text("0")
        except OSError as e:
            log.warning("aegis: no cgroup for session %s: %s", log_id, e)
            return None
        return d

    def release(self, log_id: str) -> None:
        """Kill what is left in the session's cgroup and remove it. A process
        dies a moment after SIGKILL, so a busy rmdir is retried later; one the
        next spawn got to first fails harmlessly, as the cgroup is in use."""
        d = self.path(log_id)
        if not d.is_dir():
            return
        try:
            if (d / "cgroup.kill").exists():
                (d / "cgroup.kill").write_text("1")
            else:
                for p in _procs(d):
                    os.kill(p, signal.SIGKILL)
        except OSError:
            pass
        self._rmdir(d, RMDIR_TRIES)

    def _rmdir(self, d: Path, tries: int) -> None:
        try:
            self.remove(d)
        except FileNotFoundError:
            pass
        except OSError:
            if tries > 1:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.call_later(RETRY_RMDIR_S, self._rmdir, d, tries - 1)

    # A cgroup is removed with rmdir though it lists files; the fake tree in
    # the tests swaps this for a recursive delete.
    remove: Callable[[Path], None] = staticmethod(os.rmdir)
