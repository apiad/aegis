from pathlib import Path

from aegis.events import SessionMeta
from aegis.state.history import SessionHistoryRow, list_history
from aegis.state.session_log import (
    append_meta, new_log_id, replay_events, session_log_path,
)


def _meta(handle: str, profile: str = "claude-sonnet",
          provider: str = "claude-code",
          created_at: str = "2026-05-28T14:00:00Z",
          preview: str = "a real turn") -> SessionMeta:
    # A non-empty preview by default: a header with no preview and no events
    # is a spawned-but-unused tab, which list_history deliberately drops.
    return SessionMeta(
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=created_at, origin="tui", preview=preview)


def _put(sd, handle: str, **kw) -> None:
    """Write a header into ``handle``'s log, legacy-named (log id == handle)
    so these stay exercised against the pre-log-id files on disk."""
    append_meta(sd, handle, _meta(handle, **kw))


def test_append_meta_writes_meta_as_first_record(tmp_path: Path):
    sd = tmp_path / "state"
    m = SessionMeta(
        handle="h1", profile="p1", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:00:00Z",
        origin="tui", preview="",
    )
    append_meta(sd, "h1", m)
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
    append_meta(sd, "h1", m)
    assert session_log_path(sd, "h1").exists()


def test_list_history_returns_one_row_per_meta_file(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "h1")
    _put(sd, "h2")
    rows = list_history(sd, live_handles=set())
    assert {r.handle for r in rows} == {"h1", "h2"}
    assert all(isinstance(r, SessionHistoryRow) for r in rows)


def test_list_history_finds_meta_that_is_not_the_first_record(tmp_path: Path):
    """The header is written on the first user turn, so anything the harness
    streamed at spawn lands ahead of it — with Claude's SessionStart hooks
    that is always. Requiring it first hid 220 of 223 real conversations."""
    from aegis.events import AssistantText, Unknown
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_event(sd, "h1", Unknown(raw='{"type":"system",'
                                       '"subtype":"hook_started"}'))
    append_event(sd, "h1", AssistantText(text="hi", usage=None))
    _put(sd, "h1")
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["h1"]
    assert rows[0].profile == "claude-sonnet"
    assert rows[0].inferred is False


def test_list_history_synthesizes_a_row_for_a_log_with_no_meta(tmp_path: Path):
    """Every log written before SessionMeta existed has no header and never
    will. Rebuild what can be rebuilt from the log itself rather than
    hiding the conversation."""
    from aegis.events import AssistantText, SystemInit
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_event(sd, "legacy-session", SystemInit(session_id="sid-9"))
    append_event(sd, "legacy-session", AssistantText(text="old talk",
                                                     usage=None))
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["legacy-session"]
    row = rows[0]
    assert row.inferred is True
    assert row.session_id == "sid-9"
    assert "old talk" in row.preview
    assert row.created_at  # taken from the first record's timestamp


def test_synthesized_rows_take_the_caller_s_fallback_profile(tmp_path: Path):
    """A log with no header can't say which agent ran it. Defaulting to the
    caller's default agent makes it resumable; a wrong guess just fails at
    drv.resume, which the modal already reports."""
    from aegis.events import AssistantText
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    append_event(sd, "legacy", AssistantText(text="x", usage=None))
    rows = list_history(sd, live_handles=set(),
                        fallback_profile="opus", fallback_provider="claude-code")
    assert rows[0].profile == "opus"
    assert rows[0].provider == "claude-code"


def test_real_meta_beats_the_fallback(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "h1", profile="gemini-pro", provider="gemini")
    rows = list_history(sd, live_handles=set(),
                        fallback_profile="opus", fallback_provider="claude-code")
    assert (rows[0].profile, rows[0].provider) == ("gemini-pro", "gemini")


def test_row_shows_the_current_handle_after_a_rename(tmp_path: Path):
    """A rename moves nothing on disk — it appends a header carrying the new
    name. The row must show that name while the identity stays put."""
    sd = tmp_path / "state"
    log_id = new_log_id("born-bland")
    append_meta(sd, log_id, _meta("born-bland"))
    append_meta(sd, log_id, _meta("renamed-later", preview=""))
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["renamed-later"]
    assert rows[0].log_id == log_id


def test_two_logs_for_one_recycled_handle_stay_two_rows(tmp_path: Path):
    """The pool hands a handle back out as soon as its session dies. Before
    log ids both sessions shared one file; now they are two rows."""
    from datetime import datetime, timezone
    sd = tmp_path / "state"
    may = new_log_id("candid-cerf",
                     now=datetime(2026, 5, 29, tzinfo=timezone.utc))
    july = new_log_id("candid-cerf",
                      now=datetime(2026, 7, 15, tzinfo=timezone.utc))
    append_meta(sd, may, _meta("candid-cerf", preview="the may one"))
    append_meta(sd, july, _meta("candid-cerf", preview="the july one"))
    rows = list_history(sd, live_handles=set())
    assert len(rows) == 2
    assert {r.log_id for r in rows} == {may, july}
    assert {r.preview for r in rows} == {"the may one", "the july one"}


def test_spawn_and_first_turn_headers_fold_to_one_row(tmp_path: Path):
    """Two SessionMeta records per session by design: one at spawn, one
    carrying the preview. They must not become two rows, and created_at must
    stay the spawn time."""
    sd = tmp_path / "state"
    _put(sd, "h1", created_at="2026-05-28T14:00:00Z",
                          preview="")   # spawn header: no preview yet
    append_meta(sd, "h1", SessionMeta(
        handle="h1", profile="claude-sonnet", provider="claude-code",
        cwd="/tmp", created_at="2026-05-28T14:09:00Z", origin="tui",
        preview="the first thing I asked"))
    rows = list_history(sd, live_handles=set())
    assert len(rows) == 1
    assert rows[0].created_at == "2026-05-28T14:00:00Z"
    assert rows[0].preview == "the first thing I asked"
    assert rows[0].inferred is False


def test_list_history_skips_an_empty_log(tmp_path: Path):
    """A spawned-but-never-used session is not a conversation."""
    from aegis.state.session_log import session_log_path
    sd = tmp_path / "state"
    p = session_log_path(sd, "never-spoke")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    assert list_history(sd, live_handles=set()) == []


def test_list_history_marks_open_rows(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "h1")
    _put(sd, "h2")
    rows = list_history(sd, live_handles={"h1"})
    by_handle = {r.handle: r for r in rows}
    assert by_handle["h1"].is_open is True
    assert by_handle["h2"].is_open is False


def test_list_history_sorted_most_recent_first(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "old", created_at="2026-05-28T10:00:00Z")
    _put(sd, "new", created_at="2026-05-28T14:00:00Z")
    rows = list_history(sd, live_handles=set())
    assert [r.handle for r in rows] == ["new", "old"]


def test_list_history_returns_empty_when_no_sessions_dir(tmp_path: Path):
    sd = tmp_path / "state"
    rows = list_history(sd, live_handles=set())
    assert rows == []


def test_list_history_caps_at_limit(tmp_path: Path):
    sd = tmp_path / "state"
    for i in range(5):
        _put(sd, f"h{i}", created_at=f"2026-05-28T1{i}:00:00Z")
    rows = list_history(sd, live_handles=set(), limit=2)
    assert len(rows) == 2
    assert [r.handle for r in rows] == ["h4", "h3"]


def test_list_history_session_id_latches_latest_system_init(tmp_path: Path):
    from aegis.events import SystemInit
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    _put(sd, "h1")
    append_event(sd, "h1", SystemInit(session_id="first"))
    append_event(sd, "h1", SystemInit(session_id="second"))
    rows = list_history(sd, live_handles=set())
    assert rows[0].session_id == "second"


def test_list_history_crash_inferred_when_no_close_marker(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "h1")
    rows = list_history(sd, live_handles=set())
    assert rows[0].closed_at is None
    assert rows[0].crash_inferred is True


def test_list_history_closed_marker_clears_crash_inferred(tmp_path: Path):
    from aegis.events import SessionClosed
    from aegis.state.session_log import append_event
    sd = tmp_path / "state"
    _put(sd, "h1")
    append_event(sd, "h1", SessionClosed(
        closed_at="2026-05-28T15:00:00Z", reason="user"))
    rows = list_history(sd, live_handles=set())
    assert rows[0].closed_at == "2026-05-28T15:00:00Z"
    assert rows[0].crash_inferred is False
