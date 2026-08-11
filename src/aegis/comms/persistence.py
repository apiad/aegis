"""The comms ledger: one append-only JSONL per day, per aegis instance.

Per instance rather than per handle — the whole value of the record is the
cross-agent view of who spoke to whom.

Writes go through ``queue.jsonl.append_record``, which already creates the
parent directory and stamps the schema version. Reads do NOT go through its
``read_records``: that one calls ``json.loads`` per line and raises on a
truncated trailing record, which is right for queue replay (a corrupt
lifecycle log should stop the boot) and wrong here, where a torn line must
cost one record and nothing else.
"""
from __future__ import annotations

import json
from pathlib import Path

from aegis.comms.models import Envelope
from aegis.queue.jsonl import append_record


class CommsLedger:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "comms"

    def path(self, day: str) -> Path:
        return self._root / f"{day}.jsonl"

    def write(self, env: Envelope) -> None:
        # The day comes off the envelope's own timestamp, so the ledger
        # never reads a clock of its own and a replayed run files where the
        # live one did.
        append_record(self.path(env.ts[:10]), env.to_record())

    def read(self, day: str) -> list[dict]:
        path = self.path(day)
        if not path.is_file():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def days(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl"))

    def read_all(self) -> list[dict]:
        rows: list[dict] = []
        for day in self.days():
            rows.extend(self.read(day))
        return rows
