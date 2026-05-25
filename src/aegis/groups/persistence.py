"""JSONL lifecycle log for groups.

Per-group append-only log under ``<state_dir>/groups/<name>.jsonl``.
Same shape conventions as the queue substrate (``aegis/queue/jsonl.py``):
- One JSON object per line.
- Each record carries ``kind`` + ``at`` (ISO-8601) + payload fields.
- Append is atomic-ish (single ``write+flush``); concurrent crashes
  may leave a torn last line — replay tolerates that by skipping
  unparseable trailing lines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def event_created(name: str, sender: str, at: str) -> dict[str, Any]:
    return {"kind": "created", "name": name, "sender": sender, "at": at}


def event_member_added(handle: str, profile: str, sender: str,
                       at: str) -> dict[str, Any]:
    return {"kind": "member_added", "handle": handle, "profile": profile,
            "sender": sender, "at": at}


def event_member_removed(handle: str, reason: str,
                         at: str) -> dict[str, Any]:
    return {"kind": "member_removed", "handle": handle, "reason": reason,
            "at": at}


def event_broadcast_started(broadcast_id: str, objective: str,
                            output_format: str, tool_guidance: str,
                            boundaries: str, sender: str,
                            members: tuple[str, ...]) -> dict[str, Any]:
    return {
        "kind": "broadcast_started",
        "broadcast_id": broadcast_id,
        "objective": objective,
        "output_format": output_format,
        "tool_guidance": tool_guidance,
        "boundaries": boundaries,
        "sender": sender,
        "members": list(members),
    }


def event_member_result(broadcast_id: str, handle: str, status: str,
                        text_preview: str, tokens_in: int,
                        tokens_out: int, turn_ms: int) -> dict[str, Any]:
    return {
        "kind": "member_result",
        "broadcast_id": broadcast_id,
        "handle": handle, "status": status,
        "text_preview": text_preview,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "turn_ms": turn_ms,
    }


def event_broadcast_completed(broadcast_id: str, mode: str, reducer: str,
                              at: str) -> dict[str, Any]:
    return {"kind": "broadcast_completed", "broadcast_id": broadcast_id,
            "mode": mode, "reducer": reducer, "at": at}


def event_renamed(old: str, new: str, at: str) -> dict[str, Any]:
    return {"kind": "renamed", "old": old, "new": new, "at": at}


def event_dissolved(reason: str, at: str) -> dict[str, Any]:
    return {"kind": "dissolved", "reason": reason, "at": at}


class PersistedGroupLog:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "groups"
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self, group: str) -> Path:
        return self._root / f"{group}.jsonl"

    def write(self, group: str, record: dict[str, Any]) -> None:
        p = self.path(group)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")))
            f.write("\n")

    def read(self, group: str) -> list[dict[str, Any]]:
        p = self.path(group)
        if not p.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in p.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def all_groups(self) -> list[str]:
        return sorted(p.stem for p in self._root.glob("*.jsonl"))
