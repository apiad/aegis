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
child process never parks. Without a cgroup per session, a re-parented child is found by the
AEGIS_SESSION its harness passed down (harness.session_env)."""

from __future__ import annotations

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
