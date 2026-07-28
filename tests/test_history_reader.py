from pathlib import Path

from aegis.events import SessionMeta
from aegis.state.session_log import (
    append_meta, replay_events, session_log_path,
)


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
