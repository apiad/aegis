"""Each session's CPU and RAM, sampled every few seconds (#762).

A session's load is what its harness and everything the harness started use.
With a session cgroup (cgroups.py) the kernel has attributed every process,
and the sampler reads ``cpu.stat`` and ``memory.current``. Without one, as on
a server started from a terminal, it walks ``/proc`` once per sample: a
process belongs to its nearest ancestor that is an open session's harness,
else to the session its ``AEGIS_SESSION`` names. The variable catches work
the agent detached: a background test run whose shell exited is reparented
to init or the user's systemd and leaves the tree, but keeps its
environment. A value naming no open session here (a test server's fake
session) attributes nothing, and the server's own process is never counted.

CPU is in cores over the last ``WINDOW_S``. A process's time is
``utime + stime + cutime + cstime``: the two child fields carry children that
ended and were waited for between samples, so a one-second compiler still
counts. A child seen in an earlier sample was counted then, so when it is
gone and its parent is alive (the parent waited for it), its last total is
taken off the parent's growth. RSS is summed, shared pages twice: the figure
ranks sessions, it does not account for the host's memory.

Sampled every ``INTERVAL_S`` while the server runs, in a thread, since a
notice needs the last minute even when no browser watches. Nothing is
stored; history is #235's.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .host import GB
from .quota.core import cancel_and_wait

INTERVAL_S = 5.0
WINDOW_S = 60.0
CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
ENV_KEY = b"AEGIS_SESSION="


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    start: int  # clock ticks after boot: with the pid, names one process
    ticks: int  # utime + stime + cutime + cstime
    rss: int  # bytes


def parse_stat(pid: int, text: str) -> Proc | None:
    """One ``/proc/<pid>/stat`` line. The command name may hold spaces and
    parentheses, so the fields are counted from the last ``)``."""
    try:
        f = text.rsplit(")", 1)[1].split()
        return Proc(
            pid=pid,
            ppid=int(f[1]),
            start=int(f[19]),
            ticks=int(f[11]) + int(f[12]) + int(f[13]) + int(f[14]),
            rss=int(f[21]) * PAGE,
        )
    except (IndexError, ValueError):
        return None


def env_session(environ: bytes) -> str | None:
    for item in environ.split(b"\0"):
        if item.startswith(ENV_KEY):
            return item[len(ENV_KEY) :].decode(errors="replace") or None
    return None


def attribute(
    procs: dict[int, Proc],
    harness: dict[int, str],
    env_of: Callable[[Proc], str | None],
    sessions: set[str],
    skip: int,
) -> dict[int, str]:
    """Which open session each process belongs to: the nearest ancestor that
    is a harness, else the session its environment names. ``skip`` (the
    server) and what only it explains are left out."""
    owner: dict[int, str | None] = {skip: None, **harness}
    for pid in procs:
        path, p, found = [], pid, None
        while True:
            if p in owner:
                found = owner[p]
                break
            path.append(p)
            parent = procs.get(p)
            if parent is None or parent.ppid <= 1:
                break
            p = parent.ppid
        for q in path:
            owner[q] = found
    out = {}
    for pid, s in owner.items():
        if pid == skip or pid not in procs:
            continue
        if s is None:
            s = env_of(procs[pid])
            if s not in sessions:
                continue
        out[pid] = s
    return out


class LoadSampler:
    def __init__(
        self,
        sessions: Callable[[], dict[str, tuple[int | None, Path | None]]],
        changed: Callable[[str], None],
        *,
        proc: Path = Path("/proc"),
        interval: float = INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        own_pid: int | None = None,
    ) -> None:
        self._sessions = sessions  # open log_id -> (harness pid, its cgroup)
        self._changed = changed  # a session's shown reading changed
        self._proc = proc
        self._interval = interval
        self._clock = clock
        self._own = os.getpid() if own_pid is None else own_pid
        self._prev: dict[tuple[int, int], Proc] | None = None
        self._env: dict[tuple[int, int], str | None] = {}
        self._cg_prev: dict[str, int] = {}
        self._used: dict[str, deque[tuple[float, float]]] = {}  # (t, cpu s)
        self._rss: dict[str, int] = {}
        self._shown: dict[str, tuple | None] = {}
        self._task: asyncio.Task | None = None

    # -- reading ---------------------------------------------------------------
    def of(self, log_id: str) -> dict | None:
        """``{"cpu": cores, "rss_gb": GB}`` for a session with a reading."""
        if log_id not in self._rss:
            return None
        return {"cpu": self.cores(log_id), "rss_gb": round(self._rss[log_id] / GB, 1)}

    def cores(self, log_id: str) -> float:
        h = self._used.get(log_id)
        if not h or len(h) < 2:
            return 0.0
        (t0, c0), (t1, c1) = h[0], h[-1]
        return round((c1 - c0) / (t1 - t0), 1) if t1 > t0 else 0.0

    def all(self) -> dict[str, dict]:
        return {s: r for s in list(self._rss) if (r := self.of(s)) is not None}

    # -- sampling --------------------------------------------------------------
    def sample(self, sessions: dict[str, tuple[int | None, Path | None]]) -> list[str]:
        """One pass: read, attribute, add to each session's window. Returns
        the sessions whose shown reading changed. Touches no session, so it
        runs in a thread."""
        now = self._clock()
        live = {s: pid for s, (pid, _) in sessions.items() if pid}
        cgroups = {s: d for s, (pid, d) in sessions.items() if pid and d is not None}
        used: dict[str, float] = {}
        rss: dict[str, int] = {}
        for s, d in cgroups.items():
            try:
                usec = _usage_usec((d / "cpu.stat").read_text())
                mem = int((d / "memory.current").read_text())
            except (OSError, ValueError):
                continue
            prev = self._cg_prev.get(s)
            self._cg_prev[s] = usec
            used[s] = 0.0 if prev is None else max(0, usec - prev) / 1e6
            rss[s] = mem
        rest = {s: pid for s, pid in live.items() if s not in cgroups}
        if rest:
            u, r = self._walk(rest)
            used |= u
            rss |= r
        changed = [s for s in list(self._rss) if s not in live and self._forget(s)]
        for s in live:
            if s not in rss:
                continue
            h = self._used.setdefault(s, deque())
            h.append((now, (h[-1][1] if h else 0.0) + used.get(s, 0.0)))
            while len(h) > 2 and h[1][0] <= now - WINDOW_S:
                h.popleft()
            self._rss[s] = rss[s]
        for s in live:
            r = self.of(s)
            key = None if r is None else (r["cpu"], r["rss_gb"])
            if self._shown.get(s) != key:
                self._shown[s] = key
                changed.append(s)
        return changed

    def _forget(self, s: str) -> bool:
        """Drop a session that has no process; True when it showed a reading."""
        for m in (self._used, self._rss, self._cg_prev):
            m.pop(s, None)
        return self._shown.pop(s, None) is not None

    def _walk(self, harness_of: dict[str, int]) -> tuple[dict, dict]:
        procs: dict[int, Proc] = {}
        for name in os.listdir(self._proc):
            if not name.isdigit():
                continue
            try:
                text = (self._proc / name / "stat").read_text()
            except OSError:
                continue
            if (p := parse_stat(int(name), text)) is not None:
                procs[p.pid] = p
        harness = {pid: s for s, pid in harness_of.items()}
        owner = attribute(procs, harness, self._env_of, set(harness_of), self._own)
        prev, first = self._prev or {}, self._prev is None
        now = {(p.pid, p.start): p for p in procs.values()}
        alive = set(procs)
        used: dict[str, float] = {}
        rss: dict[str, int] = {}
        for pid, s in owner.items():
            p = procs[pid]
            rss[s] = rss.get(s, 0) + p.rss
            before = prev.get((pid, p.start))
            if before is not None:
                grew = p.ticks - before.ticks
            else:
                grew = 0 if first else p.ticks
            used[s] = used.get(s, 0.0) + grew / CLK_TCK
        # A child counted before and since waited for by a live parent: its
        # last total is inside the parent's growth already.
        for key, gone in prev.items():
            if key in now or gone.ppid not in alive:
                continue
            if (s := owner.get(gone.ppid)) is not None:
                used[s] = used.get(s, 0.0) - gone.ticks / CLK_TCK
        self._prev = now
        self._env = {k: v for k, v in self._env.items() if k in now}
        for s in harness_of:
            rss.setdefault(s, 0)
        return {s: max(0.0, v) for s, v in used.items()}, rss

    def _env_of(self, p: Proc) -> str | None:
        key = (p.pid, p.start)
        if key not in self._env:
            try:
                raw = (self._proc / str(p.pid) / "environ").read_bytes()
            except OSError:
                raw = b""
            self._env[key] = env_session(raw)
        return self._env[key]

    # -- the loop --------------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                changed = await asyncio.to_thread(self.sample, self._sessions())
            except OSError:
                changed = []  # no /proc (macOS): no readings
            for s in changed:
                self._changed(s)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_and_wait(task)


def _usage_usec(text: str) -> int:
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == "usage_usec":
            return int(value)
    raise ValueError("no usage_usec")
