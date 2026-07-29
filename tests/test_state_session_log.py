# tests/test_state_session_log.py
import json

import pytest

from aegis.events import (
    AssistantText, AssistantThinking, Result, SystemInit, ThinkingTokens,
    TokenUsage, ToolResult, ToolUse,
)
from aegis.state.session_log import (
    EventReplay, LogRenameConflict, append_event, make_session_log_observer,
    rename_log, replay_events, session_log_path,
)


def test_thinking_token_estimate_survives_round_trip(tmp_path):
    h = "keen-knuth"
    append_event(tmp_path, h, AssistantThinking(text="", token_estimate=6050))
    ev = replay_events(tmp_path, h).events[0]
    assert isinstance(ev, AssistantThinking)
    assert ev.token_estimate == 6050


def test_observer_skips_thinking_tokens(tmp_path):
    # High-volume transient events must not be persisted (they'd bloat the
    # log and drift the seq index); the cumulative estimate rides on the
    # AssistantThinking block instead.
    obs = make_session_log_observer(tmp_path, "h")
    obs(None, ThinkingTokens(estimated=250, delta=100))
    obs(None, AssistantThinking(text="", token_estimate=250))
    kinds = [type(e).__name__ for e in replay_events(tmp_path, "h").events]
    assert kinds == ["AssistantThinking"]


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


def test_make_session_log_observer_appends(tmp_path):
    from aegis.state.session_log import make_session_log_observer
    obs = make_session_log_observer(tmp_path, "obs-handle")
    obs(object(), AssistantText(text="persisted", usage=None))
    r = replay_events(tmp_path, "obs-handle")
    assert [type(e).__name__ for e in r.events] == ["AssistantText"]
    assert r.events[0].text == "persisted"


def test_envelope_carries_version_and_timestamp(tmp_path):
    import json
    h = "h"
    append_event(tmp_path, h, SystemInit(session_id="x"))
    line = session_log_path(tmp_path, h).read_text().strip()
    rec = json.loads(line)
    assert rec["v"] == 1
    assert "aegis_ts" in rec
    assert rec["event"]["t"] == "SystemInit"


# ---------- atomicity of the write path ------------------------------
#
# The corruption these guard against was observed in the wild: 7 of 223
# logs in a real state dir carried either a NUL run (an append whose size
# extension outlived a crash but whose data never reached disk) or a
# record torn mid-string. Both then sat *interior* to the file, because a
# resumed session keeps appending to the same log.


def test_record_never_contains_a_raw_newline(tmp_path):
    """One record == one line is the framing invariant the reader leans on.
    ensure_ascii keeps embedded newlines escaped as \\n, so a payload full of
    them still occupies exactly one line."""
    h = "h"
    append_event(tmp_path, h, AssistantText(text="a\nb\r\nc d", usage=None))
    raw = session_log_path(tmp_path, h).read_bytes()
    assert raw.count(b"\n") == 1
    assert raw.endswith(b"\n")


def test_concurrent_appends_never_tear(tmp_path):
    """Two writers on one log must not interleave mid-record. Records are
    sized past the old 8 KiB text-buffer flush threshold, which is where the
    buffered writer used to split a record into two write() calls."""
    import threading
    h = "h"
    big = "x" * 12000

    def worker(tag: str) -> None:
        for i in range(20):
            append_event(tmp_path, h,
                         AssistantText(text=f"{tag}{i}{big}", usage=None))

    ts = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    lines = [ln for ln in
             session_log_path(tmp_path, h).read_text().splitlines() if ln]
    assert len(lines) == 40
    for ln in lines:
        json.loads(ln)  # every line is a whole record


def test_turn_barriers_are_fsynced(tmp_path, monkeypatch):
    """fsync is what stops a crash from leaving a NUL hole. Doing it per
    event would put a disk flush on the render hot path, so it happens on
    turn boundaries — bounding loss to the in-flight turn, which
    ``interrupted`` already models."""
    import aegis.state.session_log as sl
    synced: list[int] = []
    monkeypatch.setattr(sl.os, "fsync", lambda fd: synced.append(fd))

    append_event(tmp_path, "h", AssistantText(text="mid-turn", usage=None))
    assert synced == []
    append_event(tmp_path, "h", ToolUse(name="Read", summary="x"))
    assert synced == []
    append_event(tmp_path, "h", Result(duration_ms=1, is_error=False))
    assert len(synced) == 1


# ---------- damaged-log tolerance ------------------------------------


def _corrupt(tmp_path, handle: str, mutate) -> None:
    p = session_log_path(tmp_path, handle)
    p.write_text(mutate(p.read_text()), encoding="utf-8")


def test_replay_never_raises_on_garbage(tmp_path):
    h = "h"
    append_event(tmp_path, h, AssistantText(text="one", usage=None))
    _corrupt(tmp_path, h, lambda t: t + "not json at all\n")
    r = replay_events(tmp_path, h)
    assert [e.text for e in r.events] == ["one"]
    assert r.damaged == 1


def test_replay_keeps_records_after_a_damaged_interior_line(tmp_path):
    """The failure that made real conversations unrecoverable: damage in the
    middle of the log, with good turns on both sides of it."""
    h = "h"
    append_event(tmp_path, h, AssistantText(text="before", usage=None))
    _corrupt(tmp_path, h, lambda t: t + "\x00" * 400 + "\n")
    append_event(tmp_path, h, AssistantText(text="after", usage=None))
    r = replay_events(tmp_path, h)
    assert [e.text for e in r.events] == ["before", "after"]
    assert r.damaged == 1


def test_replay_salvages_record_behind_a_nul_run(tmp_path):
    """Observed shape: a lost region backfilled with NULs, immediately
    followed by an intact record on the same line. The record is still
    there — recover it rather than dropping the turn."""
    h = "h"
    append_event(tmp_path, h, AssistantText(text="survivor", usage=None))
    p = session_log_path(tmp_path, h)
    p.write_text("\x00" * 875 + p.read_text(), encoding="utf-8")
    r = replay_events(tmp_path, h)
    assert [e.text for e in r.events] == ["survivor"]
    assert r.damaged == 1
    assert r.recovered == 1


def test_replay_salvages_record_glued_to_a_torn_one(tmp_path):
    """A torn record swallows the newline, so the *next* record lands on the
    same line. Without salvage a single tear costs two turns."""
    h = "h"
    append_event(tmp_path, h, AssistantText(text="whole", usage=None))
    p = session_log_path(tmp_path, h)
    torn = '{"v":1,"aegis_ts":"2026-07-29T00:00:00.0Z","event":{"t":"Assist'
    p.write_text(torn + p.read_text(), encoding="utf-8")
    r = replay_events(tmp_path, h)
    assert [e.text for e in r.events] == ["whole"]
    assert r.recovered == 1


def test_replay_survives_invalid_utf8(tmp_path):
    """A tear inside a multi-byte sequence must not make the file unreadable."""
    h = "h"
    append_event(tmp_path, h, AssistantText(text="ok", usage=None))
    p = session_log_path(tmp_path, h)
    p.write_bytes(p.read_bytes() + b"\xff\xfe broken\n")
    r = replay_events(tmp_path, h)
    assert [e.text for e in r.events] == ["ok"]
    assert r.damaged == 1


# ---------- the log follows its handle -------------------------------


def test_rename_log_moves_the_transcript(tmp_path):
    append_event(tmp_path, "old-name", AssistantText(text="kept", usage=None))
    rename_log(tmp_path, "old-name", "new-name")
    assert not session_log_path(tmp_path, "old-name").exists()
    assert [e.text for e in replay_events(tmp_path, "new-name").events] \
        == ["kept"]


def test_rename_log_is_a_noop_for_a_session_with_no_log_yet(tmp_path):
    rename_log(tmp_path, "old-name", "new-name")  # must not raise
    assert not session_log_path(tmp_path, "new-name").exists()


def test_rename_log_refuses_to_clobber_a_stored_transcript(tmp_path):
    """The handle is the log's identity, so a name whose log already exists
    would either destroy that conversation or fabricate a shared prefix for
    two unrelated ones. Refuse; the caller picks another name."""
    append_event(tmp_path, "old-name", AssistantText(text="mine", usage=None))
    append_event(tmp_path, "taken", AssistantText(text="someone else's",
                                                  usage=None))
    with pytest.raises(LogRenameConflict):
        rename_log(tmp_path, "old-name", "taken")
    assert [e.text for e in replay_events(tmp_path, "taken").events] \
        == ["someone else's"]
    assert [e.text for e in replay_events(tmp_path, "old-name").events] \
        == ["mine"]


def test_observer_writes_to_the_live_handle_after_a_rename(tmp_path):
    """The observer used to capture the handle in its closure, so a renamed
    session kept appending to its old file while workspace.json recorded the
    new one — resume then found nothing and opened an empty pane."""
    class _Sess:
        handle = "old-name"

    sess = _Sess()
    obs = make_session_log_observer(tmp_path, "old-name")
    obs(sess, AssistantText(text="before", usage=None))

    rename_log(tmp_path, "old-name", "new-name")
    sess.handle = "new-name"
    obs(sess, AssistantText(text="after", usage=None))

    assert [e.text for e in replay_events(tmp_path, "new-name").events] \
        == ["before", "after"]
    assert not session_log_path(tmp_path, "old-name").exists()


def test_clean_log_reports_no_damage(tmp_path):
    h = "h"
    append_event(tmp_path, h, AssistantText(text="one", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))
    r = replay_events(tmp_path, h)
    assert (r.damaged, r.recovered) == (0, 0)
