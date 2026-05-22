# tests/test_state_session_log.py
from aegis.events import (
    AssistantText, Result, SystemInit, TokenUsage, ToolResult, ToolUse,
)
from aegis.state.session_log import (
    EventReplay, append_event, replay_events, session_log_path,
)


def test_path_is_handle_scoped(tmp_path):
    assert session_log_path(tmp_path, "lucid-knuth") == \
        tmp_path / "sessions" / "lucid-knuth.jsonl"


def test_append_then_replay_returns_events(tmp_path):
    h = "lucid-knuth"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    append_event(tmp_path, h, AssistantText(text="hi", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))
    r = replay_events(tmp_path, h)
    assert isinstance(r, EventReplay)
    assert [type(e).__name__ for e in r.events] == [
        "SystemInit", "AssistantText", "Result"]
    assert r.interrupted is False


def test_replay_missing_returns_empty(tmp_path):
    r = replay_events(tmp_path, "ghost")
    assert r.events == []
    assert r.interrupted is False


def test_replay_marks_interrupted_when_no_result_after_assistant(tmp_path):
    h = "wry-hopper"
    append_event(tmp_path, h, SystemInit(session_id="xyz"))
    append_event(tmp_path, h, AssistantText(text="started…", usage=None))
    # No Result — process died mid-turn.
    r = replay_events(tmp_path, h)
    assert r.interrupted is True
    # Events still returned in full; renderer decides how to mark.
    assert [type(e).__name__ for e in r.events] == [
        "SystemInit", "AssistantText"]


def test_replay_not_interrupted_if_last_was_result(tmp_path):
    h = "h"
    append_event(tmp_path, h, AssistantText(text="x", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))
    assert replay_events(tmp_path, h).interrupted is False


def test_replay_not_interrupted_for_idle_session(tmp_path):
    """A session that only saw SystemInit (no turns yet) is not 'interrupted'."""
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    assert replay_events(tmp_path, h).interrupted is False


def test_replay_skips_blank_lines(tmp_path):
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="abc"))
    p = session_log_path(tmp_path, h)
    p.write_text(p.read_text() + "\n\n")
    assert len(replay_events(tmp_path, h).events) == 1


def test_envelope_carries_version_and_timestamp(tmp_path):
    import json
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="x"))
    line = session_log_path(tmp_path, h).read_text().strip()
    rec = json.loads(line)
    assert rec["v"] == 1
    assert "aegis_ts" in rec
    assert rec["event"]["t"] == "SystemInit"
