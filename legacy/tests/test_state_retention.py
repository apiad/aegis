"""Archiving old transcripts.

Nothing pruned the state dir: 237 logs / 619 MB accumulated at ~9 MB/day,
and every cost that scales with the corpus grows with it. A transcript is
the only copy of a conversation, so this compresses in place rather than
deleting — and only closed logs, only on an explicit `aegis doctor` run.
"""
from __future__ import annotations

import gzip
import os
import time
from pathlib import Path

from aegis.events import AssistantText, SessionClosed, SessionMeta
from aegis.state.session_log import (
    append_event, archive_old_logs, replay_events, scan_log,
    session_log_path,
)


def _meta(handle="h") -> SessionMeta:
    return SessionMeta(handle=handle, profile="default",
                       provider="claude-code", cwd="/tmp",
                       created_at="2026-01-01T00:00:00Z", origin="user",
                       preview="hi")


def _closed_log(state_dir: Path, log_id: str, *, age_days: float) -> Path:
    append_event(state_dir, log_id, _meta())
    for i in range(20):
        append_event(state_dir, log_id, AssistantText(f"line {i}" * 50))
    append_event(state_dir, log_id,
                 SessionClosed(closed_at="2026-01-02T00:00:00Z",
                               reason="user"))
    p = session_log_path(state_dir, log_id)
    old = time.time() - age_days * 86400
    os.utime(p, (old, old))
    return p


def test_an_old_closed_log_is_compressed_in_place(tmp_path):
    p = _closed_log(tmp_path, "old-one", age_days=120)
    before = p.stat().st_size

    result = archive_old_logs(tmp_path, older_than_days=90)

    assert result.archived == 1
    assert not p.exists()
    gz = p.with_suffix(".jsonl.gz")
    assert gz.exists()
    assert gz.stat().st_size < before          # it is smaller
    assert result.bytes_saved > 0


def test_an_archived_log_still_reads(tmp_path):
    """Compression must not cost access — resume and Ctrl+R still work."""
    _closed_log(tmp_path, "old-one", age_days=120)
    expected = [e.text for e in replay_events(tmp_path, "old-one").events
                if hasattr(e, "text")]
    archive_old_logs(tmp_path, older_than_days=90)

    got = [e.text for e in replay_events(tmp_path, "old-one").events
           if hasattr(e, "text")]
    assert got == expected
    assert scan_log(session_log_path(tmp_path, "old-one")).records


def test_a_recent_log_is_left_alone(tmp_path):
    p = _closed_log(tmp_path, "recent", age_days=3)
    assert archive_old_logs(tmp_path, older_than_days=90).archived == 0
    assert p.exists()


def test_an_open_log_is_left_alone_however_old(tmp_path):
    """No SessionClosed marker means it may still be live, or it crashed
    and is resumable. Either way it is not ours to compress."""
    append_event(tmp_path, "open-one", _meta())
    append_event(tmp_path, "open-one", AssistantText("still going"))
    p = session_log_path(tmp_path, "open-one")
    old = time.time() - 400 * 86400
    os.utime(p, (old, old))

    assert archive_old_logs(tmp_path, older_than_days=90).archived == 0
    assert p.exists()


def test_a_live_handle_is_left_alone(tmp_path):
    """Belt and braces: a session open in this very app is never touched,
    marker or not."""
    _closed_log(tmp_path, "20260101T000000000000Z-busy-bee", age_days=200)
    result = archive_old_logs(tmp_path, older_than_days=90,
                              live_handles={"busy-bee"})
    assert result.archived == 0


def test_dry_run_reports_without_touching_anything(tmp_path):
    p = _closed_log(tmp_path, "old-one", age_days=120)
    result = archive_old_logs(tmp_path, older_than_days=90, dry_run=True)
    assert result.archived == 1
    assert p.exists()                          # still there
    assert not p.with_suffix(".jsonl.gz").exists()


def test_archiving_is_idempotent(tmp_path):
    _closed_log(tmp_path, "old-one", age_days=120)
    assert archive_old_logs(tmp_path, older_than_days=90).archived == 1
    assert archive_old_logs(tmp_path, older_than_days=90).archived == 0


def test_a_failed_archive_leaves_the_original(tmp_path, monkeypatch):
    """Never trade a readable transcript for a broken one."""
    from aegis.state import session_log as sl
    p = _closed_log(tmp_path, "old-one", age_days=120)

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(gzip, "open", boom)
    result = archive_old_logs(tmp_path, older_than_days=90)
    assert result.archived == 0
    assert p.exists()
    assert replay_events(tmp_path, "old-one").events
