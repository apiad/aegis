"""JSONL records shared by the rig, the fake agents and the metrics reader.

Each process appends its own file, so no writer ever contends with another
for the same fd. ``MarkerSeq`` is the one shared resource: several fake
agents (one per tab) must never mint the same marker.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, rec: dict) -> None:
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def read_jsonl(path: Path) -> list[dict]:
    """Every whole record in ``path``; a missing file is empty, and a
    damaged line (a writer killed mid-write) is skipped, not raised."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


class MarkerSeq:
    """A counter shared by every process that opens the same file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def next(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            n = int(fh.read().strip() or 0) + 1
            fh.seek(0)
            fh.truncate()
            fh.write(str(n))
        return n
