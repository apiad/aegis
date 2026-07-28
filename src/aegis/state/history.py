"""History reader: glob per-session event logs, fold each into one row.

Files whose first record is not a ``SessionMeta`` are excluded — that is
the gating mechanism that keeps queue-worker / workflow-spawn logs out of
the Ctrl+H listing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from aegis.events import (
    AssistantText, AssistantThinking, SessionClosed, SessionMeta, SystemInit,
)
from aegis.state.event_codec import decode_event


@dataclass(frozen=True)
class SessionHistoryRow:
    handle: str
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


def _fold_file(
    path: Path,
) -> tuple[SessionMeta, str, str | None, str | None, str] | None:
    """Fold one log into (meta, last_ts, closed_at, session_id, preview),
    or None when the file has no SessionMeta first record."""
    meta: SessionMeta | None = None
    last_ts: str = ""
    closed_at: str | None = None
    session_id: str | None = None
    preview: str = ""
    first_line = True
    try:
        with path.open("r", encoding="utf-8") as f:
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
                if first_line:
                    first_line = False
                    if ev_dict.get("t") != "SessionMeta":
                        return None
                try:
                    ev = decode_event(ev_dict)
                except (ValueError, KeyError):
                    continue
                ts = rec.get("aegis_ts", "")
                if ts > last_ts:
                    last_ts = ts
                if isinstance(ev, SessionMeta):
                    meta = ev
                    if ev.preview:
                        preview = ev.preview
                elif isinstance(ev, SessionClosed):
                    closed_at = ev.closed_at
                elif isinstance(ev, SystemInit):
                    if ev.session_id:
                        session_id = ev.session_id
                elif isinstance(ev, (AssistantText, AssistantThinking)):
                    if not preview and ev.text:
                        preview = ev.text[:200]
    except OSError:
        return None
    if meta is None:
        return None
    return meta, last_ts or meta.created_at, closed_at, session_id, preview


def list_history(state_dir_path: Path, *, live_handles: set[str],
                 limit: int = 500) -> list[SessionHistoryRow]:
    sessions_dir = state_dir_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    rows: list[SessionHistoryRow] = []
    for p in sessions_dir.glob("*.jsonl"):
        folded = _fold_file(p)
        if folded is None:
            continue
        meta, last_ts, closed_at, session_id, preview = folded
        is_open = meta.handle in live_handles
        rows.append(SessionHistoryRow(
            handle=meta.handle,
            profile=meta.profile,
            provider=meta.provider,
            cwd=meta.cwd,
            created_at=meta.created_at,
            closed_at=closed_at,
            last_activity_at=last_ts,
            preview=preview,
            session_id=session_id,
            is_open=is_open,
            crash_inferred=(closed_at is None and not is_open),
        ))
    rows.sort(key=lambda r: r.last_activity_at, reverse=True)
    return rows[:limit]
