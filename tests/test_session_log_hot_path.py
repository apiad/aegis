"""The per-event write path, and the checks around closing a session.

Persisting an event ran mkdir + open + write + close every time — 270 µs
per event on the event loop, vs 50 µs with the fd held open. And closing a
tab decoded the entire transcript to evaluate two booleans: 937 ms on a
25 MB log. The durability contract (one os.write on an O_APPEND fd, fsync
only on turn barriers) is deliberate and stays exactly as it was.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aegis.events import (
    AssistantText, Result, SessionClosed, SessionMeta,
)
from aegis.state.session_log import (
    append_event, make_session_log_observer, replay_events,
)


def test_observer_opens_the_log_once_for_many_events(tmp_path, monkeypatch):
    real_open = os.open
    opens: list[str] = []

    def counting_open(path, *a, **kw):
        opens.append(str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(os, "open", counting_open)
    obs = make_session_log_observer(tmp_path, "log-1")
    for i in range(50):
        obs(None, AssistantText(f"chunk {i}"))

    assert len(opens) == 1, f"opened the log {len(opens)} times for 50 events"
    replayed = replay_events(tmp_path, "log-1").events
    assert len(replayed) == 50
    assert replayed[-1].text == "chunk 49"


def test_observer_still_fsyncs_on_a_turn_barrier(tmp_path, monkeypatch):
    synced: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    obs = make_session_log_observer(tmp_path, "log-2")
    obs(None, AssistantText("streaming"))
    assert synced == []                      # not a barrier
    obs(None, Result(duration_ms=1, is_error=False))
    assert len(synced) == 1                  # barrier


def test_observer_recovers_if_the_log_is_replaced_underneath_it(tmp_path):
    """aegis doctor --repair rewrites logs. Writes through a held fd keep
    succeeding into the orphaned inode, so the fd is re-validated on turn
    barriers — losing at most the turn in progress, not the session."""
    obs = make_session_log_observer(tmp_path, "log-3")
    obs(None, AssistantText("before"))
    (tmp_path / "sessions" / "log-3.jsonl").unlink()

    obs(None, Result(duration_ms=1, is_error=False))    # barrier: revalidates
    obs(None, AssistantText("after"))
    texts = [getattr(e, "text", None)
             for e in replay_events(tmp_path, "log-3").events]
    assert "after" in texts


def test_append_event_still_works_standalone(tmp_path):
    """Non-hot-path callers (the SessionClosed marker) use it directly."""
    append_event(tmp_path, "log-4", AssistantText("x"))
    assert replay_events(tmp_path, "log-4").events


# --------------------------------------------------------------------------
# Closing a tab
# --------------------------------------------------------------------------

def test_close_marker_checks_do_not_decode_the_transcript(
        tmp_path, monkeypatch):
    """The two questions — is there a header, is there already a close
    marker — are answerable from the raw records."""
    from aegis.state import session_log as sl
    from aegis.tui.app import needs_close_marker

    append_event(tmp_path, "log-5", _meta())
    for i in range(200):
        append_event(tmp_path, "log-5", AssistantText(f"chunk {i}"))

    monkeypatch.setattr(sl, "replay_events", _boom)
    assert needs_close_marker(tmp_path, "log-5") is True


def test_close_marker_is_skipped_for_worker_logs(tmp_path):
    """No SessionMeta header means it was never a user tab."""
    from aegis.tui.app import needs_close_marker
    append_event(tmp_path, "log-6", AssistantText("worker output"))
    assert needs_close_marker(tmp_path, "log-6") is False


def test_close_marker_is_not_written_twice(tmp_path):
    from aegis.tui.app import needs_close_marker
    append_event(tmp_path, "log-7", _meta())
    assert needs_close_marker(tmp_path, "log-7") is True
    append_event(tmp_path, "log-7",
                 SessionClosed(closed_at="2026-07-29T01:00:00Z",
                               reason="user"))
    assert needs_close_marker(tmp_path, "log-7") is False


def test_close_marker_skipped_for_a_log_that_does_not_exist(tmp_path):
    from aegis.tui.app import needs_close_marker
    assert needs_close_marker(tmp_path, "never-existed") is False


def _meta() -> SessionMeta:
    return SessionMeta(handle="h", profile="default",
                       provider="claude-code", cwd="/tmp",
                       created_at="2026-07-29T00:00:00Z", origin="user")


def _boom(*a, **kw):
    raise AssertionError("decoded the whole transcript to answer a boolean")
