"""Which daemons are running, across roots.

`aegis ls` lists daemons for every project, so the record cannot live under
a project. One JSON file per daemon under ``~/.aegis/daemons/``, named by a
hash of the root so a path with a separator in it still names one file.

The file is a hint; the pid is the truth. A record survives SIGKILL, a
process does not, and a stale file that made ``aegis`` refuse to autostart
would be the worst possible failure of this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DaemonRecord:
    root: Path
    pid: int
    socket: Path
    started: float
    version: str = "0"


def registry_dir() -> Path:
    override = os.environ.get("AEGIS_DAEMON_DIR")
    if override:
        return Path(override)
    return Path.home() / ".aegis" / "daemons"


def _key(root: Path) -> str:
    return hashlib.sha256(
        str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]


def _file(root: Path) -> Path:
    return registry_dir() / f"{_key(root)}.json"


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    return True


def record(rec: DaemonRecord) -> Path:
    p = _file(rec.root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": str(Path(rec.root).resolve()),
        "pid": rec.pid,
        "socket": str(rec.socket),
        "started": rec.started,
        "version": rec.version,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def forget(root: Path) -> None:
    # The exit path runs from a finally block that may never have recorded.
    try:
        _file(root).unlink()
    except FileNotFoundError:
        pass


def _read(p: Path) -> DaemonRecord | None:
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return DaemonRecord(
            root=Path(raw["root"]), pid=int(raw["pid"]),
            socket=Path(raw["socket"]), started=float(raw["started"]),
            version=str(raw.get("version", "0")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # One damaged file must not make `aegis ls` unusable everywhere.
        return None


def _discard(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def live_daemons() -> list[DaemonRecord]:
    """Every running daemon. Prunes records whose process is gone."""
    out: list[DaemonRecord] = []
    d = registry_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        rec = _read(p)
        if rec is None or not is_alive(rec.pid):
            _discard(p)
            continue
        out.append(rec)
    return out


def daemon_for(root: Path) -> DaemonRecord | None:
    p = _file(root)
    rec = _read(p) if p.is_file() else None
    if rec is None or not is_alive(rec.pid):
        if p.is_file():
            _discard(p)
        return None
    return rec


def kill(rec: DaemonRecord, sig: int = signal.SIGTERM) -> bool:
    """Signal a daemon. Returns False when it was already gone."""
    try:
        os.kill(rec.pid, sig)
    except ProcessLookupError:
        forget(rec.root)
        return False
    return True


def now() -> float:
    return time.time()
