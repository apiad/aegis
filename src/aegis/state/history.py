"""History reader: glob per-session event logs, fold each into one row.

A log's ``SessionMeta`` header is the authoritative description of the
session, but it is written on the first *user* turn, so anything the
harness streamed at spawn lands ahead of it — with Claude's
``SessionStart`` hooks, always. Requiring it to be the first record
therefore hid essentially every real conversation, so it is looked up
wherever it sits.

A log with no header at all still holds a conversation (every session
predating ``SessionMeta`` is in that state, and always will be), so a
row is synthesized from what the log itself carries and flagged
``inferred``. What can't be recovered — which agent profile ran it — is
filled from the caller's fallback, since a wrong guess only costs a
failed ``drv.resume`` that the modal already reports, while dropping the
row costs the conversation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, SessionClosed, SessionMeta, SystemInit,
    ToolUse,
)
from aegis.state.event_codec import decode_event
from aegis.state.session_log import parse_log_id


@dataclass(frozen=True)
class SessionHistoryRow:
    log_id: str              # identity on disk; never changes
    handle: str              # current display name; a rename moves this
    profile: str
    provider: str
    cwd: str
    created_at: str
    closed_at: str | None
    last_activity_at: str
    preview: str
    session_id: str | None
    is_open: bool
    crash_inferred: bool
    inferred: bool = False   # rebuilt from the log; had no SessionMeta


@dataclass(frozen=True)
class _Fold:
    meta: SessionMeta | None
    last_handle: str | None
    first_ts: str
    last_ts: str
    closed_at: str | None
    session_id: str | None
    preview: str
    has_content: bool


def _fold_file(path: Path) -> _Fold | None:
    """Reduce one log to the fields a history row needs, or None when the
    file holds no readable record at all."""
    meta: SessionMeta | None = None
    last_handle: str | None = None
    first_ts = last_ts = ""
    closed_at: str | None = None
    session_id: str | None = None
    preview = ""
    saw_record = False
    has_content = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ev_dict = rec.get("event")
                if not isinstance(ev_dict, dict):
                    continue
                try:
                    ev = decode_event(ev_dict)
                except (ValueError, KeyError):
                    continue
                saw_record = True
                ts = rec.get("aegis_ts", "")
                if ts and not first_ts:
                    first_ts = ts
                if ts > last_ts:
                    last_ts = ts
                if isinstance(ev, SessionMeta):
                    # A session writes one header at spawn and another on
                    # its first user turn. Keep the first (its created_at is
                    # the real one) and take the preview from whichever
                    # carries it.
                    if meta is None:
                        meta = ev
                    # A rename appends a header carrying the new name, so
                    # the last one always holds the current handle.
                    last_handle = ev.handle
                    if ev.preview and not preview:
                        preview = ev.preview
                        has_content = True   # the user said something
                elif isinstance(ev, SessionClosed):
                    closed_at = ev.closed_at
                elif isinstance(ev, SystemInit):
                    if ev.session_id:
                        session_id = ev.session_id
                elif isinstance(ev, (AssistantText, AssistantThinking)):
                    has_content = True
                    if not preview and ev.text:
                        preview = ev.text[:200]
                elif isinstance(ev, ToolUse):
                    has_content = True
    except OSError:
        return None
    if not saw_record:
        return None
    return _Fold(meta=meta, last_handle=last_handle, first_ts=first_ts,
                 last_ts=last_ts, closed_at=closed_at, session_id=session_id,
                 preview=preview, has_content=has_content)


def list_history(state_dir_path: Path, *, live_handles: set[str],
                 limit: int = 500, fallback_profile: str = "",
                 fallback_provider: str = "") -> list[SessionHistoryRow]:
    sessions_dir = state_dir_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    rows: list[SessionHistoryRow] = []
    for p in sessions_dir.glob("*.jsonl"):
        fold = _fold_file(p)
        if fold is None or not fold.has_content:
            # A tab that was spawned and closed without a word is not a
            # conversation. Every boot opens a default tab, so without this
            # the listing fills up with blanks.
            continue
        meta = fold.meta
        log_id = p.stem
        born_at, born_handle = parse_log_id(log_id)
        # Display the session's *current* name (the last header wins, and a
        # rename appends one), falling back to the name it was born with.
        handle = fold.last_handle or born_handle
        created_at = (born_at or (meta.created_at if meta else fold.first_ts))
        is_open = handle in live_handles
        rows.append(SessionHistoryRow(
            log_id=log_id,
            handle=handle,
            profile=meta.profile if meta else fallback_profile,
            provider=meta.provider if meta else fallback_provider,
            cwd=meta.cwd if meta else "",
            created_at=created_at,
            closed_at=fold.closed_at,
            last_activity_at=fold.last_ts or created_at,
            preview=fold.preview,
            session_id=fold.session_id,
            is_open=is_open,
            crash_inferred=(fold.closed_at is None and not is_open),
            inferred=meta is None,
        ))
    rows.sort(key=lambda r: r.last_activity_at, reverse=True)
    return rows[:limit]
