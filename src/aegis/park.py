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

from dataclasses import dataclass
from pathlib import Path

from .cgroups import _ppid, _procs
from .harness import SESSION_VAR

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
