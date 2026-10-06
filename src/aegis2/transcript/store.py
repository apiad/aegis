"""The append-only transcript store: one JSON record per line.

A record is either a raw Claude stdout line (``src: "claude"``) or something
aegis2 did (``src: "aegis2"``: spawn, send, interrupt, exit, close). Records
carry their own index ``i``, so skipping a damaged line on load never shifts
the ids of the entries after it. Entries are not stored; they are folded from
these records (``entries.py``), so a better summary applies to old transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = path.open("a", encoding="utf-8")
        self._next = 0

    def append(self, record: dict) -> dict:
        """Write ``record`` with its index and return the stored form."""
        stored = {"i": self._next, **record}
        self._next += 1
        self._f.write(json.dumps(stored, ensure_ascii=False) + "\n")
        self._f.flush()
        return stored

    def close(self) -> None:
        self._f.close()


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
