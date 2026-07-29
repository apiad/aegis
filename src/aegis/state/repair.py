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

from aegis.state.session_log import scan_log, session_log_path


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


def repair_all(state_dir_path: Path) -> list[LogReport]:
    """Repair every damaged, non-live log. Returns what was rewritten."""
    done: list[LogReport] = []
    for r in survey(state_dir_path):
        if r.healthy or r.live:
            continue
        done.append(repair_log(r.path))
    return done


__all__ = ["LogReport", "repair_all", "repair_log", "session_log_path",
           "survey"]
