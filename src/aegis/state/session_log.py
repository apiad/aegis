"""Per-tab event-stream persistence for transcript replay on resume.

Mirrors the queue/inbox JSONL envelope: each line is
``{"v": 1, "aegis_ts": <iso>, "event": <encoded-event>}``. Replay
returns the decoded ``Event`` list plus an ``interrupted`` flag set
when the file ends after an assistant turn with no terminating
``Result`` — used by the renderer to mark the last turn ⚠ interrupted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, ToolUse,
)
from aegis.state.event_codec import decode_event, encode_event

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EventReplay:
    events: list[Event]
    interrupted: bool


def session_log_path(state_dir_path: Path, handle: str) -> Path:
    return state_dir_path / "sessions" / f"{handle}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def append_event(state_dir_path: Path, handle: str, ev: Event) -> None:
    p = session_log_path(state_dir_path, handle)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"v": SCHEMA_VERSION, "aegis_ts": _now_iso(),
           "event": encode_event(ev)}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


# Event types that indicate an in-progress turn (must be followed by a
# Result to be considered complete).
_TURN_EVENTS = (AssistantText, AssistantThinking, ToolUse)


def replay_events(state_dir_path: Path, handle: str) -> EventReplay:
    p = session_log_path(state_dir_path, handle)
    if not p.exists():
        return EventReplay(events=[], interrupted=False)
    events: list[Event] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events.append(decode_event(rec["event"]))
    interrupted = False
    if events:
        # Scan backwards: was the last "non-Result" event part of a turn?
        last_turn_evt = None
        for e in reversed(events):
            if isinstance(e, Result):
                break
            if isinstance(e, _TURN_EVENTS):
                last_turn_evt = e
                break
        interrupted = last_turn_evt is not None
    return EventReplay(events=events, interrupted=interrupted)
