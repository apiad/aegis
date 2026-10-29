"""OpenCode servers whose owner is gone, found and ended at ``aegis serve``'s
start (#620).

Each ``opencode serve`` child carries ``OWNER`` in its environment: the pid of
the process that started it and that pid's start time, so a reused pid does
not read as the owner alive. A child whose owner has ended is of no use to
anyone (only its owner knows its password) and holds about 300 MB, so the
sweep ends it. The kernel normally ends it first (``launch``'s
``die_with_parent``); the sweep catches what that cannot, such as a host
without setpriv, and the servers versions before #620 left behind. Those carry
no ``OWNER``: one is taken for orphaned when it has an ``AEGIS_SESSION``, the
argv aegis starts it with, and was re-parented to init or a systemd user
manager, which is where an orphan lands and never where aegis starts one.

Only processes this server's user can read are seen: in the multi-user mode
another user's child is left to the kernel.
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

log = logging.getLogger("aegis.opencode")

OWNER = "AEGIS_OPENCODE_OWNER"
PROC = Path("/proc")
# How process.py's argv ends.
SERVE_ARGS = ["serve", "--hostname", "127.0.0.1", "--port", "0"]


def _stat(pid: int | str, proc: Path) -> list[str] | None:
    """The fields of /proc/<pid>/stat after the command name, or None when
    the process is gone."""
    try:
        raw = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    return raw.rpartition(")")[2].split()


def _started(pid: int | str, proc: Path = PROC) -> str | None:
    f = _stat(pid, proc)
    return f[19] if f and len(f) > 19 else None  # field 22, starttime


def owner_tag() -> str:
    """This process as an owner: its pid and start time."""
    return f"{os.getpid()}:{_started(os.getpid())}"


def _orphaned(pid: str, proc: Path) -> bool:
    d = proc / pid
    try:
        env = dict(
            kv.split("=", 1)
            for kv in (d / "environ").read_bytes().decode(errors="replace").split("\0")
            if "=" in kv
        )
        argv = (d / "cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return False  # gone, or another user's
    # The server itself, never what it started: its tools inherit OWNER too.
    if argv[-6:-1] != SERVE_ARGS:
        return False
    if OWNER in env:
        owner, _, started = env[OWNER].partition(":")
        return _started(owner, proc) != started
    if "AEGIS_SESSION" not in env:
        return False
    f = _stat(pid, proc)
    if not f:
        return False
    ppid = f[1]
    if ppid == "1":
        return True
    try:
        return (proc / ppid / "comm").read_text().strip() == "systemd"
    except OSError:
        return False


def orphans(proc: Path = PROC) -> list[int]:
    """The OpenCode servers whose owner is gone, by pid."""
    me = str(os.getpid())
    return [
        int(p.name)
        for p in proc.iterdir()
        if p.name.isdigit() and p.name != me and _orphaned(p.name, proc)
    ]


def sweep(proc: Path = PROC) -> list[int]:
    """SIGTERM every orphaned OpenCode server; the pids it signalled."""
    done = []
    for pid in orphans(proc):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        done.append(pid)
    if done:
        log.warning("ended %d orphaned opencode serve process(es): %s", len(done), done)
    return done
