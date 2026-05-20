"""Append-only JSONL state log used by QueueManager and InboxRouter.

One JSON object per line; lazily parsed on replay. Each record carries a
``v: <schema-version>`` field so future migrations can fan out — readers
preserve unknown ``v`` values rather than failing, so a v1 reader can
silently skip-or-pass unknown fields from a v2 producer.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 1


def append_record(path: Path, record: dict) -> None:
    """Append one JSON object to ``path``, creating parent dirs as needed.

    Stamps ``v: SCHEMA_VERSION`` unless the caller supplied an explicit
    ``v`` (used by tests for forward-compat scenarios).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {"v": SCHEMA_VERSION, **record}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, separators=(",", ":")) + "\n")


def read_records(path: Path) -> list[dict]:
    """Return all records as dicts. Returns ``[]`` if the file is missing."""
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out
