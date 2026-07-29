"""Per-tab event-stream persistence for transcript replay on resume.

Mirrors the queue/inbox JSONL envelope: each line is
``{"v": 1, "aegis_ts": <iso>, "event": <encoded-event>}``. Replay
returns the decoded ``Event`` list plus an ``interrupted`` flag set
when the file ends after an assistant turn with no terminating
``Result`` — used by the renderer to mark the last turn ⚠ interrupted.

Durability. A transcript is the only copy of a conversation, so the
write path is atomic per record and the read path never gives up on a
whole log because part of it is damaged:

* every record is one ``os.write`` on an ``O_APPEND`` fd, so concurrent
  writers cannot interleave mid-record and a large record is never split
  across two syscalls by a user-space buffer;
* ``fsync`` runs on turn barriers (see ``_BARRIER``), which bounds a
  crash's data loss to the in-flight turn — the case ``interrupted``
  already models. Per-event fsync would put a disk flush on the render
  hot path for no extra safety that matters;
* damaged lines are skipped rather than raised on, and whole records
  still embedded in them (behind a NUL run, or glued on after a torn
  record) are salvaged. ``EventReplay`` reports both counts so the
  renderer can say so instead of silently shortening a conversation.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, SessionClosed,
    SessionMeta, SystemInit, ThinkingTokens, ToolUse,
)
from aegis.state.event_codec import decode_event, encode_event

SCHEMA_VERSION = 1

# Byte-for-byte prefix of every record we write (``json.dumps`` with
# these separators and this key order). Nested objects inside a record
# are JSON strings, so their quotes are escaped — the literal sequence
# can therefore only occur at a genuine record boundary, which makes it
# a safe resync marker when scanning a damaged line.
RECORD_START = '{"v":1,"aegis_ts":"'

# Events that end a unit of work worth surviving a crash. Everything
# before one of these is on disk once it returns.
_BARRIER = (SessionMeta, SystemInit, Result, SessionClosed)

_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class EventReplay:
    events: list[Event]
    interrupted: bool
    damaged: int = 0      # lines that did not parse as a whole record
    recovered: int = 0    # records salvaged out of those lines


@dataclass(frozen=True)
class LogScan:
    """Raw record dicts read off a log, plus what it cost to get them."""
    records: list[dict] = field(default_factory=list)
    damaged: int = 0
    recovered: int = 0


def session_log_path(state_dir_path: Path, handle: str) -> Path:
    return state_dir_path / "sessions" / f"{handle}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def encode_record(ev: Event) -> bytes:
    """One event → one newline-terminated record. ``ensure_ascii`` (the
    default) keeps every newline in the payload escaped, so a record can
    never span two lines."""
    rec = {"v": SCHEMA_VERSION, "aegis_ts": _now_iso(),
           "event": encode_event(ev)}
    return (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8")


def _write_record(fd: int, blob: bytes) -> None:
    view = memoryview(blob)
    while view:
        view = view[os.write(fd, view):]


def append_event(state_dir_path: Path, handle: str, ev: Event) -> None:
    p = session_log_path(state_dir_path, handle)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = encode_record(ev)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _write_record(fd, blob)
        if isinstance(ev, _BARRIER):
            os.fsync(fd)
    finally:
        os.close(fd)


def append_meta(state_dir_path: Path, meta) -> None:
    """Write a SessionMeta as the (intended-first) record of a handle's log.
    Thin alias over append_event that names the intent — the caller owns the
    'must be first record' invariant (a fresh user-initiated session)."""
    append_event(state_dir_path, meta.handle, meta)


def make_session_log_observer(state_dir_path, handle: str):
    """Returns an EventCb that appends every event to the per-handle JSONL.
    Persistence must never break the live render, so it swallows errors."""
    def _obs(_sess, ev) -> None:
        # ThinkingTokens are high-volume transient counter nudges (hundreds
        # per turn); the cumulative estimate is stamped onto the block's
        # AssistantThinking, which we do persist — so skip these.
        if isinstance(ev, ThinkingTokens):
            return
        try:
            append_event(state_dir_path, handle, ev)
        except Exception:
            pass
    return _obs


def _salvage(line: str) -> list[dict]:
    """Whole records still readable inside a line that did not parse.

    Covers both observed corruptions: a NUL run (or any junk) in front of
    an intact record, and a torn record that ate its newline so the next
    record landed on the same line.
    """
    out: list[dict] = []
    pos = line.find(RECORD_START)
    while pos != -1:
        try:
            rec, end = _DECODER.raw_decode(line, pos)
        except ValueError:
            pos = line.find(RECORD_START, pos + 1)
            continue
        if isinstance(rec, dict):
            out.append(rec)
        pos = line.find(RECORD_START, end)
    return out


def scan_log(path: Path) -> LogScan:
    """Read every record off a log, tolerating damage. Never raises for a
    readable file; a missing one scans as empty."""
    if not path.exists():
        return LogScan()
    records: list[dict] = []
    damaged = recovered = 0
    # errors="replace": a tear inside a multi-byte sequence must not make
    # the rest of the transcript unreadable.
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            if isinstance(rec, dict):
                records.append(rec)
                continue
            damaged += 1
            salvaged = _salvage(line)
            recovered += len(salvaged)
            records.extend(salvaged)
    return LogScan(records=records, damaged=damaged, recovered=recovered)


# Event types that indicate an in-progress turn (must be followed by a
# Result to be considered complete).
_TURN_EVENTS = (AssistantText, AssistantThinking, ToolUse)


def replay_events(state_dir_path: Path, handle: str) -> EventReplay:
    scan = scan_log(session_log_path(state_dir_path, handle))
    events: list[Event] = []
    damaged = scan.damaged
    for rec in scan.records:
        try:
            events.append(decode_event(rec["event"]))
        except Exception:  # noqa: BLE001 — one unreadable record, not a log
            damaged += 1
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
    return EventReplay(events=events, interrupted=interrupted,
                       damaged=damaged, recovered=scan.recovered)
