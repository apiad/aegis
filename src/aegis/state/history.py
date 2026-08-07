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

import contextlib
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
    title: str = ""          # display label; "" when never set
    title_source: str = ""   # "" | auto | agent | human


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
    title: str = ""
    title_source: str = ""


def _fold_file(path: Path) -> _Fold | None:
    """Reduce one log to the fields a history row needs, or None when the
    file holds no readable record at all."""
    meta: SessionMeta | None = None
    last_handle: str | None = None
    first_ts = last_ts = ""
    closed_at: str | None = None
    session_id: str | None = None
    preview = ""
    title = ""
    title_source = ""
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
                    # Last *non-empty* wins, not simply last: a rename
                    # appends a header that re-derives every field, so a
                    # plain last-wins would blank a title the operator set
                    # (see tui/app.py:_record_rename).
                    if ev.title:
                        title = ev.title
                        title_source = ev.title_source
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
                 preview=preview, has_content=has_content,
                 title=title, title_source=title_source)


INDEX_NAME = "history_index.json"
# 2: folds gained title / title_source.
#
# **This bump was a mistake — do not copy the reasoning.** It was made on
# the theory that a cached v1 fold, knowing nothing about titles, would
# leave pre-existing sessions permanently titleless. It would not:
# `_entry_to_fold` reads the two fields with `.get(..., "")`, and a log
# only *gains* a title by appending a SessionMeta, which changes its size
# and mtime and so busts that entry's stamp anyway. No log written before
# 0.32.0 could carry a title, because the field did not exist. The stamp
# check already covered every case.
#
# The cost is real and falls on the user: discarding the index re-folds
# the whole corpus on the next Ctrl+R — 767MB / 332 logs measured ~60s on
# zion, during which the modal does not appear at all. Warm is 0.07s.
#
# So: bump this ONLY when a change makes an existing entry actively
# *wrong*, not merely incomplete. Adding a field that defaults sanely and
# is refreshed by the stamp is not a reason.
INDEX_VERSION = 2


def _index_path(state_dir_path: Path) -> Path:
    return state_dir_path / INDEX_NAME


def _load_index(state_dir_path: Path) -> dict:
    """The cached folds, or an empty map. A damaged index costs a full
    re-scan, never a wrong listing."""
    try:
        raw = json.loads(_index_path(state_dir_path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
        return {}
    entries = raw.get("entries")
    return entries if isinstance(entries, dict) else {}


def _save_index(state_dir_path: Path, entries: dict) -> None:
    tmp = _index_path(state_dir_path).with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": INDEX_VERSION, "entries": entries}),
        encoding="utf-8")
    tmp.replace(_index_path(state_dir_path))


def _fold_to_entry(fold: _Fold) -> dict:
    meta = fold.meta
    return {
        "meta": None if meta is None else {
            "handle": meta.handle, "profile": meta.profile,
            "provider": meta.provider, "cwd": meta.cwd,
            "created_at": meta.created_at, "origin": meta.origin,
            "preview": meta.preview,
        },
        "last_handle": fold.last_handle,
        "title": fold.title, "title_source": fold.title_source,
        "first_ts": fold.first_ts, "last_ts": fold.last_ts,
        "closed_at": fold.closed_at, "session_id": fold.session_id,
        "preview": fold.preview, "has_content": fold.has_content,
    }


def _entry_to_fold(entry: dict) -> "_Fold | None":
    try:
        m = entry.get("meta")
        meta = None if m is None else SessionMeta(
            handle=m["handle"], profile=m["profile"], provider=m["provider"],
            cwd=m["cwd"], created_at=m["created_at"], origin=m["origin"],
            preview=m.get("preview", ""))
        return _Fold(
            meta=meta, last_handle=entry["last_handle"],
            first_ts=entry["first_ts"], last_ts=entry["last_ts"],
            closed_at=entry["closed_at"], session_id=entry["session_id"],
            preview=entry["preview"], has_content=entry["has_content"],
            title=entry.get("title", ""),
            title_source=entry.get("title_source", ""))
    except (KeyError, TypeError):
        return None


def list_history(state_dir_path: Path, *, live_handles: set[str],
                 limit: int = 500, fallback_profile: str = "",
                 fallback_provider: str = "") -> list[SessionHistoryRow]:
    sessions_dir = state_dir_path / "sessions"
    if not sessions_dir.is_dir():
        return []
    # Logs are append-only, so (size, mtime) is enough to know a cached fold
    # is still good. Without this, every Ctrl+R re-read and re-decoded the
    # entire corpus — 25s warm, 60s cold, on 615MB.
    cached = _load_index(state_dir_path)
    fresh: dict = {}
    dirty = False
    rows: list[SessionHistoryRow] = []
    for p in sessions_dir.glob("*.jsonl"):
        try:
            st = p.stat()
            stamp = [st.st_size, int(st.st_mtime_ns)]
        except OSError:
            continue
        entry = cached.get(p.name)
        fold = None
        if entry is not None and entry.get("stamp") == stamp:
            fold = _entry_to_fold(entry)
        if fold is None:
            fold = _fold_file(p)
            dirty = True
        fresh[p.name] = ({"stamp": stamp, **_fold_to_entry(fold)}
                         if fold is not None else {"stamp": stamp,
                                                   "meta": None,
                                                   "last_handle": None,
                                                   "first_ts": "",
                                                   "last_ts": "",
                                                   "closed_at": None,
                                                   "session_id": None,
                                                   "preview": "",
                                                   "has_content": False})
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
            title=fold.title,
            title_source=fold.title_source,
        ))
    if dirty or len(fresh) != len(cached):
        # Best-effort: a read-only state dir must not break the listing.
        with contextlib.suppress(Exception):
            _save_index(state_dir_path, fresh)
    rows.sort(key=lambda r: r.last_activity_at, reverse=True)
    return rows[:limit]
