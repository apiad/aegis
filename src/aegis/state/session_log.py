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

import contextlib
import json
import os
import re
import time
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


# ``<YYYYMMDD>T<HHMMSS>[uuuuuu]Z-<handle>``. The microseconds are what
# make the id an identity rather than a very likely one: two sessions can
# share a handle *and* a second (a crash-respawn loop), and second
# precision would have silently merged them — the exact bug this replaces.
# The fractional part is optional so ids minted by ``doctor --split`` off a
# legacy log, which only knows the record's second, still parse.
_LOG_ID_RE = re.compile(r"^(\d{8})T(\d{6})(\d{6})?Z-(.+)$")


def new_log_id(handle: str, *, now: datetime | None = None) -> str:
    """Mint a log id for a session: birth time + the handle it was born with.

    Handles are drawn from a finite generated pool and recycled as soon as a
    session dies, so they identify a *name*, not a conversation — on a real
    state dir 100 of 223 logs had silently accumulated several unrelated
    sessions each. The id is minted once at spawn and never changes, which
    is also why a rename moves nothing.
    """
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{ts}-{handle}"


def parse_log_id(log_id: str) -> tuple[str | None, str]:
    """``(birth time as ISO, birth handle)``.

    A bare handle is a legacy id — every log written before ids existed is
    named ``<handle>.jsonl`` — and comes back with no birth time.
    """
    m = _LOG_ID_RE.match(log_id)
    if m is None:
        return None, log_id
    d, t, micro, handle = m.groups()
    frac = f".{micro}" if micro else ""
    return (f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}{frac}Z",
            handle)


def session_log_path(state_dir_path: Path, log_id: str) -> Path:
    """Where ``log_id`` lives. Prefers the plain log; falls back to an
    archived ``.jsonl.gz`` so a compressed transcript still resumes and
    still lists (see ``archive_old_logs``)."""
    plain = state_dir_path / "sessions" / f"{log_id}.jsonl"
    if plain.exists():
        return plain
    gz = plain.with_suffix(".jsonl.gz")
    return gz if gz.exists() else plain


@dataclass(slots=True)
class ArchiveResult:
    archived: int = 0
    bytes_saved: int = 0
    skipped: int = 0


def _is_closed(path: Path) -> bool:
    """Whether the log carries a SessionClosed marker — read from the raw
    records, no decoding."""
    for rec in scan_log(path).records:
        if (rec.get("event") or {}).get("t") == "SessionClosed":
            return True
    return False


def archive_old_logs(state_dir_path: Path, *, older_than_days: float = 90.0,
                     live_handles: "set[str] | None" = None,
                     dry_run: bool = False) -> ArchiveResult:
    """Gzip closed transcripts older than ``older_than_days``, in place.

    Nothing else prunes the state dir, and it grows ~9 MB/day — every cost
    that scales with the corpus scales with that. A transcript is the only
    copy of a conversation, so this compresses rather than deletes, and
    only touches logs that carry a close marker: an open one may still be
    live, or may have crashed and still be resumable.

    Never implicit — call it from ``aegis doctor --archive``.
    """
    import gzip
    import shutil

    sessions = state_dir_path / "sessions"
    result = ArchiveResult()
    if not sessions.is_dir():
        return result
    cutoff = time.time() - older_than_days * 86400.0
    live = live_handles or set()
    for p in sorted(sessions.glob("*.jsonl")):
        try:
            if p.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        _, handle = parse_log_id(p.stem)
        if handle in live or p.stem in live:
            result.skipped += 1
            continue
        if not _is_closed(p):
            result.skipped += 1
            continue
        if dry_run:
            result.archived += 1
            continue
        gz = p.with_suffix(".jsonl.gz")
        try:
            with p.open("rb") as src, gzip.open(gz, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except Exception:      # noqa: BLE001 — never trade a good log for none
            with contextlib.suppress(OSError):
                gz.unlink()
            result.skipped += 1
            continue
        before, after = p.stat().st_size, gz.stat().st_size
        p.unlink()
        result.archived += 1
        result.bytes_saved += max(0, before - after)
    return result


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


def append_event(state_dir_path: Path, log_id: str, ev: Event) -> None:
    p = session_log_path(state_dir_path, log_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = encode_record(ev)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _write_record(fd, blob)
        if isinstance(ev, _BARRIER):
            os.fsync(fd)
    finally:
        os.close(fd)


def append_meta(state_dir_path: Path, log_id: str, meta) -> None:
    """Write a SessionMeta into ``log_id``'s log. Thin alias over
    append_event that names the intent. A session writes one at spawn, one
    on its first user turn (for the preview), and one on every rename (to
    record the new handle) — the reader folds them into a single row."""
    append_event(state_dir_path, log_id, meta)


def make_session_log_observer(state_dir_path, log_id: str):
    """Returns an EventCb that appends every event to the per-handle JSONL.

    The fd is opened once and held for the session. Re-opening per event
    cost mkdir + open + write + close — 270 µs against 50 µs — and this runs
    synchronously on the event loop, once per streamed chunk. The durability
    contract is unchanged: still one ``os.write`` on an ``O_APPEND`` fd
    (so records can't interleave with another writer), still ``fsync`` only
    on turn barriers.

    Persistence must never break the live render, so it swallows errors —
    and drops the fd on failure, so a log rewritten underneath it (``aegis
    doctor --repair``) is picked up again on the next event instead of
    wedging the session.
    """
    fd: int | None = None

    def _drop() -> None:
        nonlocal fd
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
            fd = None

    def _stale(path: Path) -> bool:
        """True when our fd no longer refers to the file at ``path``.

        Writing through a held fd keeps working after the file is unlinked
        or replaced — the bytes just go to an orphaned inode and are lost
        silently. ``aegis doctor --repair`` rewrites logs, so check on
        barriers (about twice a turn): cheap, and it bounds any loss to the
        turn in progress rather than the rest of the session.
        """
        try:
            return os.fstat(fd).st_ino != os.stat(path).st_ino
        except OSError:
            return True

    def _obs(_sess, ev) -> None:
        nonlocal fd
        # ThinkingTokens are high-volume transient counter nudges (hundreds
        # per turn); the cumulative estimate is stamped onto the block's
        # AssistantThinking, which we do persist — so skip these.
        if isinstance(ev, ThinkingTokens):
            return
        barrier = isinstance(ev, _BARRIER)
        try:
            p = session_log_path(Path(state_dir_path), log_id)
            if fd is not None and barrier and _stale(p):
                _drop()
            if fd is None:
                p.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            _write_record(fd, encode_record(ev))
            if barrier:
                os.fsync(fd)
        except Exception:
            _drop()
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
    if path.suffix == ".gz":
        import gzip
        opener = lambda: gzip.open(  # noqa: E731
            path, "rt", encoding="utf-8", errors="replace")
    else:
        opener = lambda: path.open(  # noqa: E731
            "r", encoding="utf-8", errors="replace")
    with opener() as f:
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


def replay_events(state_dir_path: Path, log_id: str) -> EventReplay:
    scan = scan_log(session_log_path(state_dir_path, log_id))
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
