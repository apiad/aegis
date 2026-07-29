from __future__ import annotations

from pathlib import Path

from aegis.events import AssistantText, ToolUse
from aegis.state.session_log import append_event, session_log_path
from aegis.web.history import read_history


def _state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def test_missing_file_returns_empty(tmp_path: Path):
    assert read_history(_state_dir(tmp_path), "ghost") == []


def test_happy_path_synthesizes_seq(tmp_path: Path):
    sd = _state_dir(tmp_path)
    append_event(sd, "h", AssistantText("one"))
    append_event(sd, "h", ToolUse(name="Read", summary="x"))
    append_event(sd, "h", AssistantText("three"))
    out = read_history(sd, "h")
    seqs = [seq for seq, _ in out]
    assert seqs == [1, 2, 3]
    assert isinstance(out[0][1], AssistantText)
    assert out[0][1].text == "one"
    assert isinstance(out[1][1], ToolUse)


def test_blank_lines_skipped(tmp_path: Path):
    sd = _state_dir(tmp_path)
    append_event(sd, "h", AssistantText("one"))
    p = session_log_path(sd, "h")
    with p.open("a", encoding="utf-8") as f:
        f.write("\n")  # stray blank line
    append_event(sd, "h", AssistantText("two"))
    out = read_history(sd, "h")
    assert [t.text for _, t in out] == ["one", "two"]


def test_torn_trailing_line_is_dropped(tmp_path: Path):
    sd = _state_dir(tmp_path)
    append_event(sd, "h", AssistantText("one"))
    append_event(sd, "h", AssistantText("two"))
    p = session_log_path(sd, "h")
    with p.open("a", encoding="utf-8") as f:
        f.write('{"v": 1, "aegis_ts": "x", "eve')  # torn write, no newline
    out = read_history(sd, "h")
    assert [t.text for _, t in out] == ["one", "two"]  # torn line dropped


def test_corrupt_interior_line_is_skipped(tmp_path: Path):
    """Interior damage is the *normal* shape once a session resumes into a
    log it already crashed in, so raising here just moved the outage from
    the transcript to the whole web session."""
    sd = _state_dir(tmp_path)
    append_event(sd, "h", AssistantText("one"))
    p = session_log_path(sd, "h")
    with p.open("a", encoding="utf-8") as f:
        f.write("\x00" * 200 + "\n")
    append_event(sd, "h", AssistantText("two"))
    out = read_history(sd, "h")
    assert [t.text for _, t in out] == ["one", "two"]


def test_seq_is_record_indexed_not_line_indexed(tmp_path: Path):
    """`SubscriptionRegistry` seeds `hs.seq = len(read_history(...))` and then
    increments per live event, so seq must number *records*. Numbering lines
    means any skipped line leaves seq > len(history) and every subsequent
    live event is dropped by the `seq > current` dedup."""
    sd = _state_dir(tmp_path)
    append_event(sd, "h", AssistantText("one"))
    p = session_log_path(sd, "h")
    with p.open("a", encoding="utf-8") as f:
        f.write("\n" + "\x00" * 64 + "\n")
    append_event(sd, "h", AssistantText("two"))
    out = read_history(sd, "h")
    assert [seq for seq, _ in out] == [1, 2]
    assert out[-1][0] == len(out)
