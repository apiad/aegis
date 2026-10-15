"""Parking: ending the harness process of a session nothing needs, so an idle
session holds no memory, and resuming it with the next message (#415).

A session parks only when ending its process kills nothing. ``strays`` lists
what would die besides the harness; ``blockers`` says why a session must stay
up; the ``Parker`` loop parks one once nothing has blocked it for its
harness's ``park:`` seconds. ``Session.park`` ends the process the way Stop
does, and ``ensure_running`` brings it back, as it does after any Stop.

The harness keeps some children for its whole life: a stdio MCP server, a
plugin's helper. Those are direct children that are not shells, and they end
and start again with it, so they are not strays. Anything under a shell (the
Bash tool's ``bash -c``), or that has left the tree (``nohup cmd &``), is.
That exemption is trusted only for Claude Code, whose shells stay in the
tree. For the other harnesses every child counts (``keep_servers=False``)
until their resume and tool spawning are measured, so a session with any
child process never parks. Without a cgroup per session, a re-parented child
is found by the AEGIS_SESSION its harness passed down (harness.session_env)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .cgroups import _ppid, _procs
from .harness import SESSION_VAR

if TYPE_CHECKING:
    from .registry import Registry
    from .session import Session

log = logging.getLogger("aegis.park")

SHELLS = frozenset({"bash", "sh", "dash", "zsh", "fish", "ksh"})


def _marked(proc: Path, log_id: str) -> list[int]:
    mark = f"{SESSION_VAR}={log_id}".encode() + b"\0"
    out = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if mark in (d / "environ").read_bytes():
                out.append(int(d.name))
        except OSError:
            continue  # gone, or another user's
    return out


def _comm(proc: Path, pid: int) -> str:
    try:
        return (proc / str(pid) / "comm").read_text().strip()
    except OSError:
        return ""


def _below(proc: Path, pid: int, top: int) -> int | None:
    """The direct child of ``top`` that ``pid`` descends from, or None when
    ``pid`` is not in ``top``'s tree."""
    child, p, seen = None, pid, set()
    while p and p not in seen:
        if p == top:
            return child
        seen.add(p)
        child, p = p, _ppid(proc, p)
    return None


def strays(
    harness_pid: int,
    log_id: str,
    cgroup: Path | None,
    proc: Path = Path("/proc"),
    keep_servers: bool = True,
) -> list[int]:
    """Processes that ending this session's harness would kill, other than the
    harness and, with ``keep_servers``, the servers it keeps."""
    pids = _procs(cgroup) if cgroup is not None else _marked(proc, log_id)
    out = []
    for pid in pids:
        if pid == harness_pid:
            continue
        child = _below(proc, pid, harness_pid) if keep_servers else None
        if child is not None and _comm(proc, child) not in SHELLS:
            continue
        out.append(pid)
    return sorted(out)


# Claude Code tools that keep a timer in the harness's memory: ending the
# process cancels it, no child process shows it was there, and its fires
# arrive only as unprompted turns, with no task event to count. Monitor and a
# background Agent emit task events, so open_tasks already holds them.
TIMER_TOOLS = frozenset({"ScheduleWakeup", "CronCreate"})
# Attentions whose next event comes from outside the harness: a person, or a
# monitor, queue task or session the server runs (registry._waits).
PARKABLE = frozenset({"done", "needs_you", "review", "waiting"})


@dataclass(frozen=True)
class Facts:
    after: (
        int | None
    )  # the park: seconds for its harness; None when the harness never parks
    running: bool
    status: str
    attention: str
    worker: bool
    held: bool  # a held message, or a flush of them under way
    open_tasks: int
    timers: frozenset[str]
    strays: tuple[int, ...]
    watchers: int
    # Its last turn ended with turn_end, or it waits on the server. The card
    # reads a turn that ended with neither as done; the idle nudge asks first.
    reported: bool = True


def blockers(f: Facts) -> list[str]:
    """Why the session must keep its process, one plain reason per rule;
    empty when it may park."""
    out = []
    if f.after is None:
        out.append("its harness does not park")
    if not f.running or f.status != "idle":
        out.append("it is not idle with a process")
    if f.attention not in PARKABLE:
        out.append(f"its attention is {f.attention}")
    if not f.reported:
        out.append("its last turn did not call turn_end")
    if f.worker:
        out.append("it is a queue worker")
    if f.held:
        out.append("it holds a message")
    if f.open_tasks:
        out.append(f"{f.open_tasks} background task(s) would die")
    if f.timers:
        out.append(f"{', '.join(sorted(f.timers))} would be cancelled")
    if f.strays:
        out.append(f"{len(f.strays)} other process(es) would die")
    if f.watchers:
        out.append("someone is looking at it")
    return out


class Clock:
    """Since when each session has had no blockers. A blocker, a reset or a
    harness that does not park starts it again."""

    def __init__(self) -> None:
        self._since: dict[str, float] = {}

    def due(self, log_id: str, clear: bool, after: int | None, now: float) -> bool:
        if not clear or after is None:
            self._since.pop(log_id, None)
            return False
        return now - self._since.setdefault(log_id, now) >= after

    def idle_for(self, log_id: str, now: float) -> float:
        return now - self._since.get(log_id, now)

    def reset(self, log_id: str) -> None:
        self._since.pop(log_id, None)


# The environment variable is for the browser tests only, whose server runs as
# a separate process and cannot patch this module.
EVERY_S = float(os.environ.get("AEGIS_PARK_EVERY_S", "30"))


class Watchers:
    """Which session each browser shows in a visible page: one entry per socket,
    or per browser behind a link (web.py fills it from ``watch`` messages)."""

    def __init__(self) -> None:
        self._at: dict[str, str] = {}
        self.on_visible: Callable[[str], None] = lambda log_id: None

    def watch(self, key: str, log_id: str | None) -> None:
        if log_id is None:
            self._at.pop(key, None)
            return
        self._at[key] = log_id
        self.on_visible(log_id)

    def drop(self, prefix: str) -> None:
        """Forget a socket, and every browser behind it when it is a link's."""
        for k in [k for k in self._at if k == prefix or k.startswith(prefix + ":")]:
            del self._at[k]

    def count(self, log_id: str) -> int:
        return sum(1 for v in self._at.values() if v == log_id)


class Parker:
    """Every EVERY_S, parks each session that has had no blockers for its
    harness's ``park:`` seconds. Beside Nudges, and as cheap: the /proc scan in
    ``strays`` runs only for a session nothing else blocks."""

    def __init__(
        self, registry: Registry, park: Callable[[], dict], watchers: Watchers
    ) -> None:
        self._registry, self._park, self._watchers = registry, park, watchers
        self.clock = Clock()
        self._task: asyncio.Task | None = None
        watchers.on_visible = self.clock.reset

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a tick must never end the loop
                log.exception("aegis: park tick failed")
            await asyncio.sleep(EVERY_S)

    def facts(self, s: Session, deep: bool) -> Facts:
        # Here, not at the top: config imports session, which imports this module.
        from .config import park_after

        reg = self._registry
        found = ()
        if deep and s.pid:
            keep = s.spec.harness == "claude-code"
            found = tuple(strays(s.pid, s.log_id, reg.cgroup_dir(s), keep_servers=keep))
        attention = reg.card(s)["attention"]
        ended = s.standing.get("ended") or {}
        return Facts(
            after=park_after(self._park(), s.spec.harness),
            running=s.running,
            status=s.status,
            attention=attention,
            worker=bool(s.worker),
            held=bool(s.held) or s._flushing,
            open_tasks=len(s.open_tasks),
            timers=frozenset(s.timers),
            strays=found,
            watchers=self._watchers.count(s.log_id),
            reported=attention == "waiting" or ended.get("how") == "reported",
        )

    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for s in list(self._registry.sessions.values()):
            f = self.facts(s, deep=False)
            # The second facts call scans /proc only when the first is clear.
            clear = not blockers(f) and not blockers(self.facts(s, deep=True))
            if not self.clock.due(s.log_id, clear, f.after, now):
                continue
            idle = self.clock.idle_for(s.log_id, now)
            self.clock.reset(s.log_id)
            if await s.park(idle):
                log.info("aegis: parked %s after %.0f s idle", s.handle, idle)
