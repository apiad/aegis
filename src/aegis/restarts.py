"""What a restart of aegis gives back: which interrupted turns continue.

aegis writes ``<state>/server.json`` at both ends of a clean shutdown and reads
it at boot, so boot can tell a restart from a crash. A turn the server's own
stop interrupted continues at the next boot: up to twice in a row after clean
restarts, once after a crash, because a turn can take the server down (#269)
and continuing it again would take it down again. A harness that failed on its
own is never restarted, because that could loop.

Under systemd's default ``KillMode=control-group`` every harness gets SIGTERM
at the same moment as aegis, and can exit before ``Session.shutdown`` marks it.
An exit by SIGTERM shortly before a clean stop began counts as that stop's.

No record is a crash: that is also the first boot after an upgrade from a
version that wrote none.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("aegis.restarts")

RECORD = "server.json"
# An exit by SIGTERM this long before a clean stop began was that stop's doing:
# uvicorn closes its connections before it runs the lifespan shutdown.
GRACE_S = 30.0
SIGTERM_CODES = (128 + signal.SIGTERM, -signal.SIGTERM)
MAX_AFTER_RESTART = 2
MAX_AFTER_CRASH = 1
RESTARTED = "The server restarted while you were working. Continue where you left off."
CRASHED = (
    "aegis stopped unexpectedly while you were working. Continue where you left off."
)
DECLINED = "restarts interrupted this turn more than once; continue it by hand"


@dataclass(frozen=True)
class Previous:
    """How the last run of this server ended."""

    clean: bool
    stopping_at: float | None = None


CLEAN = Previous(clean=True)


@dataclass(frozen=True)
class Verdict:
    go: bool
    crash: bool


def _path(state_root: Path) -> Path:
    return state_root / RECORD


def _write(state_root: Path, doc: dict) -> None:
    p = _path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc))
    tmp.replace(p)


def stopping(state_root: Path) -> float:
    """The server begins to stop, before anything is terminated."""
    t = time.time()
    _write(state_root, {"stopping_at": t})
    return t


def stopped(state_root: Path, stopping_at: float) -> None:
    """Every meta is flushed: the stop was clean."""
    _write(
        state_root,
        {"stopping_at": stopping_at, "stopped_at": time.time(), "clean": True},
    )


def started(state_root: Path) -> Previous:
    """How the last run ended; then mark this one started."""
    try:
        doc = json.loads(_path(state_root).read_text())
    except (OSError, ValueError):
        doc = None
    _write(state_root, {"started_at": time.time()})
    if isinstance(doc, dict) and doc.get("clean") is True:
        at = doc.get("stopping_at")
        return Previous(True, float(at) if isinstance(at, (int, float)) else None)
    return Previous(False)


def _interrupted(meta: dict, prev: Previous) -> bool:
    if meta.get("last_status") == "working":
        return True
    ex = meta.get("last_exit") or {}
    return bool(
        prev.clean
        and prev.stopping_at is not None
        and ex.get("code") in SIGTERM_CODES
        # The unit's SIGTERM reaches idle harnesses too: only a cut turn counts.
        and ex.get("status") == "working"
        and (ex.get("at") or 0) >= prev.stopping_at - GRACE_S
    )


def verdict(meta: dict, prev: Previous) -> Verdict | None:
    """None when the last run's end did not interrupt this session's turn."""
    if not _interrupted(meta, prev):
        return None
    crash = not prev.clean
    limit = MAX_AFTER_CRASH if crash else MAX_AFTER_RESTART
    return Verdict(go=(meta.get("restart_continues") or 0) < limit, crash=crash)


def header(crash: bool) -> str:
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"> from aegis:{'crash' if crash else 'restart'} · {at}"


def _stat(pid: int) -> tuple[str, int] | None:
    """(state, start time in clock ticks) of ``pid`` from /proc, or None."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text.rsplit(")", 1)[1].split()
        return fields[0], int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def started_at(pid: int) -> int | None:
    """When ``pid`` started, in clock ticks; None when it is gone or there is
    no /proc. A zombie has ended."""
    st = _stat(pid)
    return None if st is None or st[0] in ("Z", "X") else st[1]


def end_leftover(pid: int | None, started: int | None, wait_s: float = 3.0) -> bool:
    """End a harness a crashed server left running, so two processes never
    resume one session. Only the same process: same pid, same start time.
    True when one was ended."""
    if not pid or started is None or started_at(pid) != started:
        return False
    group = _leads_group(pid)
    _signal(pid, group, signal.SIGTERM)
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if started_at(pid) != started:
            return True
        time.sleep(0.05)
    _signal(pid, group, signal.SIGKILL)
    log.warning(
        "aegis: a leftover harness (pid %s) ignored SIGTERM and was killed", pid
    )
    return True


def _leads_group(pid: int) -> bool:
    try:
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _signal(pid: int, group: bool, sig: int) -> None:
    try:
        (os.killpg if group else os.kill)(pid, sig)
    except ProcessLookupError:
        pass
