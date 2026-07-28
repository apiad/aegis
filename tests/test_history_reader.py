from pathlib import Path

from aegis.events import SessionMeta
from aegis.state.history import SessionHistoryRow, list_history
from aegis.state.session_log import (
    append_meta, replay_events, session_log_path,
)


def _meta(handle: str, profile: str = "claude-sonnet",
          provider: str = "claude-code",
          created_at: str = "2026-05-28T14:00:00Z") -> SessionMeta:
    return SessionMeta(
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=created_at, origin="tui", preview="")


def test_append_meta_writes_meta_as_first_record(tmp_path: Path):
    sd = tmp_path / "state"
    m = SessionMeta(
        handle="h1", profile="p1", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:00:00Z",
        origin="tui", preview="",
    )
    append_meta(sd, m)
    replay = replay_events(sd, "h1")
    assert len(replay.events) == 1
    assert replay.events[0] == m


def test_append_meta_creates_sessions_directory(tmp_path: Path):
    sd = tmp_path / "state"
    m = SessionMeta(
        handle="h1", profile="p1", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:00:00Z",
        origin="tui", preview="",
    )
    append_meta(sd, m)
    assert session_log_path(sd, "h1").exists()


def test_list_history_returns_one_row_per_meta_file(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_meta(sd, _meta("h2"))
    rows = list_history(sd, live_handles=set())
    assert {r.handle for r in rows} == {"h1", "h2"}
    assert all(isinstance(r, SessionHistoryRow) for r in rows)


def test_list_history_skips_files_without_meta_header(tmp_path: Path):
    """Worker logs (no SessionMeta first record) are excluded."""
    from aegis.events import AssistantText
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_event(sd, "worker-handle", AssistantText(text="hi", usage=None))
    append_meta(sd, _meta("user-handle"))
    rows = list_history(sd, live_handles=set())
    assert {r.handle for r in rows} == {"user-handle"}


def test_list_history_marks_open_rows(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_meta(sd, _meta("h2"))
    rows = list_history(sd, live_handles={"h1"})
    by_handle = {r.handle: r for r in rows}
    assert by_handle["h1"].is_open is True
    assert by_handle["h2"].is_open is False


def test_list_history_sorted_most_recent_first(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("old", created_at="2026-05-28T10:00:00Z"))
    append_meta(sd, _meta("new", created_at="2026-05-28T14:00:00Z"))
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["new", "old"]


def test_list_history_returns_empty_when_no_sessions_dir(tmp_path: Path):
    sd = tmp_path / "state"
    rows = list_history(sd, live_handles=set())
    assert rows == []


def test_list_history_caps_at_limit(tmp_path: Path):
    sd = tmp_path / "state"
    for i in range(5):
        append_meta(sd, _meta(f"h{i}", created_at=f"2026-05-28T1{i}:00:00Z"))
    rows = list_history(sd, live_handles=set(), limit=2)
    assert len(rows) == 2
    assert [r.handle for r in rows] == ["h4", "h3"]


def test_list_history_session_id_latches_latest_system_init(tmp_path: Path):
    from aegis.events import SystemInit
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_event(sd, "h1", SystemInit(session_id="first"))
    append_event(sd, "h1", SystemInit(session_id="second"))
    rows = list_history(sd, live_handles=set())
    assert rows[0].session_id == "second"


def test_list_history_crash_inferred_when_no_close_marker(tmp_path: Path):
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    rows = list_history(sd, live_handles=set())
    assert rows[0].closed_at is None
    assert rows[0].crash_inferred is True


def test_list_history_closed_marker_clears_crash_inferred(tmp_path: Path):
    from aegis.events import SessionClosed
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_meta(sd, _meta("h1"))
    append_event(sd, "h1", SessionClosed(
        closed_at="2026-05-28T15:00:00Z", reason="user"))
    rows = list_history(sd, live_handles=set())
    assert rows[0].closed_at == "2026-05-28T15:00:00Z"
    assert rows[0].crash_inferred is False
