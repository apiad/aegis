"""Survey and repair of damaged session logs — the engine behind
``aegis doctor``.

``scan_log`` already lets a damaged log be *read*, so repair is not
required for recovery; it exists to stop a log from paying the scan
cost (and re-reporting the same damage) forever, and to give a place
where the surviving bytes are consolidated. It never deletes: the
original is kept alongside as ``<name>.jsonl.corrupt<N>``, because even
the bytes we couldn't parse may be readable by hand later.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aegis.state.session_log import (
    parse_log_id, scan_log, session_log_path,
)


@dataclass(frozen=True)
class LogReport:
    path: Path
    handle: str
    records: int
    damaged: int
    recovered: int
    live: bool = False
    backup: Path | None = None

    @property
    def healthy(self) -> bool:
        return self.damaged == 0


def _live_handles(state_dir_path: Path) -> set[str]:
    """Handles the last-saved workspace still has open. Rewriting one of
    those logs would drop everything its session appends afterwards — the
    running process holds an fd on the inode we'd replace."""
    from aegis.state.workspace import CorruptWorkspace, load
    try:
        ws = load(state_dir_path)
    except (CorruptWorkspace, OSError):
        return set()
    return {t.handle for t in ws.tabs} if ws else set()


def survey(state_dir_path: Path) -> list[LogReport]:
    """One report per session log, worst first."""
    sessions = state_dir_path / "sessions"
    if not sessions.is_dir():
        return []
    live = _live_handles(state_dir_path)
    reports = [
        LogReport(path=p, handle=p.stem, records=len(scan.records),
                  damaged=scan.damaged, recovered=scan.recovered,
                  live=p.stem in live)
        for p, scan in ((p, scan_log(p)) for p in sorted(sessions.glob("*.jsonl")))
    ]
    reports.sort(key=lambda r: (-r.damaged, r.handle))
    return reports


def _backup_path(path: Path) -> Path:
    n = 1
    while (candidate := path.with_suffix(f".jsonl.corrupt{n}")).exists():
        n += 1
    return candidate


def repair_log(path: Path) -> LogReport:
    """Rewrite ``path`` from its readable records, keeping the original.

    A clean log is left untouched (and reports ``backup=None``). Call only
    for handles with no live session — see ``LogReport.live``.
    """
    scan = scan_log(path)
    report = LogReport(path=path, handle=path.stem, records=len(scan.records),
                       damaged=scan.damaged, recovered=scan.recovered)
    if report.healthy:
        return report

    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".repair",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in scan.records:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise

    backup = _backup_path(path)
    os.replace(path, backup)
    os.replace(tmp, path)
    return LogReport(path=path, handle=path.stem, records=len(scan.records),
                     damaged=scan.damaged, recovered=scan.recovered,
                     backup=backup)


def _boundaries(records: list[dict]) -> list[int]:
    """Indices where a new upstream session starts.

    A ``SystemInit`` carrying a session_id we have not seen means the
    harness handed out a fresh conversation, which — in a log named after a
    recycled handle — means a different conversation entirely. Records
    ahead of the first boundary belong to the first session (Claude streams
    its SessionStart hooks before ``SystemInit``), so index 0 always opens.
    """
    starts = [0]
    seen: set[str] = set()
    for i, rec in enumerate(records):
        ev = rec.get("event")
        if not isinstance(ev, dict) or ev.get("t") != "SystemInit":
            continue
        sid = ev.get("session_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        if len(seen) > 1:
            starts.append(i)
    return starts


def split_log(path: Path) -> list[Path]:
    """Split a legacy log holding several sessions into one file each.

    Returns the new paths (birth-time ordered), or ``[]`` when the log holds
    a single session. Each part is named ``<first record's time>-<handle>``,
    so the pieces get the identity the originals never had. The original is
    kept as ``<name>.jsonl.split<N>``.
    """
    scan = scan_log(path)
    starts = _boundaries(scan.records)
    if len(starts) < 2:
        return []

    _, handle = parse_log_id(path.stem)
    bounds = starts + [len(scan.records)]
    written: list[Path] = []
    for lo, hi in zip(starts, bounds[1:]):
        chunk = scan.records[lo:hi]
        if not chunk:
            continue
        stamp = _stamp(chunk[0].get("aegis_ts", ""))
        target = path.with_name(f"{stamp}-{handle}.jsonl")
        n = 1
        while target.exists():
            target = path.with_name(f"{stamp}-{handle}-{n}.jsonl")
            n += 1
        with target.open("w", encoding="utf-8") as f:
            for rec in chunk:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        written.append(target)

    n = 1
    while (backup := path.with_suffix(f".jsonl.split{n}")).exists():
        n += 1
    os.replace(path, backup)
    return written


def _stamp(aegis_ts: str) -> str:
    """``2026-05-29T10:00:00.123456Z`` → ``20260529T100000Z``. Falls back to
    a zero stamp so a record with no timestamp still gets a stable name."""
    digits = "".join(c for c in aegis_ts if c.isdigit())[:14]
    if len(digits) < 14:
        digits = digits.ljust(14, "0")
    return f"{digits[:8]}T{digits[8:14]}Z"


def split_all(state_dir_path: Path) -> dict[str, list[Path]]:
    """Split every legacy log that holds more than one session. Skips logs
    belonging to a live tab and logs already using the new naming."""
    out: dict[str, list[Path]] = {}
    for r in survey(state_dir_path):
        if r.live or parse_log_id(r.path.stem)[0] is not None:
            continue
        parts = split_log(r.path)
        if parts:
            out[r.handle] = parts
    return out


def repair_all(state_dir_path: Path) -> list[LogReport]:
    """Repair every damaged, non-live log. Returns what was rewritten."""
    done: list[LogReport] = []
    for r in survey(state_dir_path):
        if r.healthy or r.live:
            continue
        done.append(repair_log(r.path))
    return done


__all__ = ["LogReport", "repair_all", "repair_log", "session_log_path",
           "split_all", "split_log", "survey"]
