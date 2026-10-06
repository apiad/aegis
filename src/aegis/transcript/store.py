"""The append-only transcript store: one JSON record per line.

A record is either a raw Claude stdout line (``src: "claude"``) or something
aegis did (``src: "aegis"``: spawn, send, interrupt, exit, close). Records
carry their own index ``i``, so skipping a damaged line on load never shifts
the ids of the entries after it. Entries are not stored; they are folded from
these records (``entries.py``), so a better summary applies to old transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path


class Store:
    """Opened lazily on the first append, so a server with hundreds of stored
    sessions holds no file open for the ones nobody writes to. A store that
    already has records continues their numbering."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._f = None
        self._next: int | None = None

    def append(self, record: dict) -> dict:
        """Write ``record`` with its index and return the stored form."""
        if self._f is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._next is None:
                self._next = last_index(self.path) + 1
            self._f = self.path.open("a", encoding="utf-8")
            if _ends_mid_line(self.path):
                self._f.write("\n")  # a crash cut the last line; start clean
        assert self._next is not None
        stored = {"i": self._next, **record}
        self._next += 1
        self._f.write(json.dumps(stored, ensure_ascii=False) + "\n")
        self._f.flush()
        return stored

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


def _ends_mid_line(path: Path) -> bool:
    with path.open("rb") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            return False
        f.seek(-1, 2)
        return f.read(1) != b"\n"


def last_index(path: Path) -> int:
    """The index of the last intact record, read from the end of the file; -1
    for a missing or empty store. A truncated last line is skipped."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return -1
    with path.open("rb") as f:
        chunk = 1 << 16
        while True:
            start = max(0, size - chunk)
            f.seek(start)
            lines = f.read(size - start).split(b"\n")
            if start > 0:
                lines = lines[1:]  # the first one may be cut
            for raw in reversed(lines):
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("i"), int):
                    return rec["i"]
            if start == 0:
                return -1
            chunk *= 4


def read_store(path: Path) -> tuple[list[dict], int]:
    """Every intact record in ``path``, and how many lines were damaged."""
    records: list[dict] = []
    damaged = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                damaged += 1
                continue
            if isinstance(rec, dict) and isinstance(rec.get("i"), int) and "src" in rec:
                records.append(rec)
            else:
                damaged += 1
    return records, damaged
