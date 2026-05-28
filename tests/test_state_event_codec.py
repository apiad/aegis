# tests/test_state_event_codec.py
import pytest
from aegis.events import (
    SystemInit, AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, Unknown, TokenUsage,
)
from aegis.state.event_codec import encode_event, decode_event


def _roundtrip(ev):
    return decode_event(encode_event(ev))


def test_system_init_roundtrip():
    e = SystemInit(session_id="abc-123")
    assert _roundtrip(e) == e


def test_assistant_text_with_usage_roundtrip():
    u = TokenUsage(input=10, cache_creation=5, cache_read=80, output=42)
    e = AssistantText(text="hi", usage=u)
    assert _roundtrip(e) == e


def test_assistant_text_no_usage_roundtrip():
    e = AssistantText(text="plain", usage=None)
    assert _roundtrip(e) == e


def test_assistant_thinking_roundtrip():
    e = AssistantThinking(text="…", usage=None)
    assert _roundtrip(e) == e


def test_tool_use_roundtrip():
    e = ToolUse(name="Read", summary="src/x.py", usage=None)
    assert _roundtrip(e) == e


def test_tool_result_roundtrip():
    e = ToolResult(text="ok", is_error=False)
    assert _roundtrip(e) == e


def test_tool_result_error_roundtrip():
    e = ToolResult(text="boom", is_error=True)
    assert _roundtrip(e) == e


def test_result_roundtrip():
    u = TokenUsage(input=1, cache_creation=2, cache_read=3, output=4)
    e = Result(duration_ms=1234, is_error=False,
               input_tokens=1, output_tokens=4, usage=u)
    assert _roundtrip(e) == e


def test_unknown_roundtrip():
    e = Unknown(raw='{"weird": true}')
    assert _roundtrip(e) == e


def test_decode_rejects_missing_type():
    with pytest.raises(ValueError):
        decode_event({"text": "no type"})


def test_decode_rejects_unknown_type():
    with pytest.raises(ValueError):
        decode_event({"t": "MysteryEvent"})


def test_tool_use_with_kind_roundtrip():
    e = ToolUse(name="Read", summary="x.py", kind="read")
    rt = _roundtrip(e)
    assert rt == e
    assert rt.kind == "read"


def test_tool_use_with_locations_roundtrip():
    e = ToolUse(name="Read", summary="x.py",
                locations=(("src/x.py", 42), ("src/y.py", None)))
    rt = _roundtrip(e)
    assert rt == e
    assert rt.locations == (("src/x.py", 42), ("src/y.py", None))


def test_tool_use_with_raw_input_roundtrip():
    e = ToolUse(name="Bash", summary="echo hi",
                raw_input={"command": "echo hi", "description": "say hi"})
    rt = _roundtrip(e)
    assert rt == e


def test_tool_use_with_all_new_fields_roundtrip():
    e = ToolUse(name="Edit", summary="x.py",
                kind="edit", tool_call_id="toolu_1",
                raw_input={"file_path": "x.py", "old_string": "a",
                           "new_string": "b"},
                locations=(("x.py", None),),
                status="completed")
    assert _roundtrip(e) == e


def test_tool_result_with_kind_and_id_roundtrip():
    e = ToolResult(text="hi", is_error=False,
                   tool_call_id="toolu_1", kind="read")
    rt = _roundtrip(e)
    assert rt == e
    assert rt.kind == "read"
    assert rt.tool_call_id == "toolu_1"


def test_legacy_tool_use_record_decodes_with_defaults():
    """Old session logs (pre-slice-1) wrote ToolUse without the new
    fields. Those records must still decode cleanly."""
    legacy = {"t": "ToolUse", "name": "Bash", "summary": "echo hi",
              "usage": None}
    ev = decode_event(legacy)
    assert isinstance(ev, ToolUse)
    assert ev.name == "Bash"
    assert ev.summary == "echo hi"
    assert ev.kind is None
    assert ev.tool_call_id is None
    assert ev.raw_input is None
    assert ev.locations == ()
    assert ev.status is None


def test_assistant_text_with_message_id_roundtrip():
    e = AssistantText(text="hi", message_id="msg_42")
    rt = _roundtrip(e)
    assert rt == e
    assert rt.message_id == "msg_42"


def test_assistant_thinking_with_message_id_roundtrip():
    e = AssistantThinking(text="hmm", message_id="msg_99")
    rt = _roundtrip(e)
    assert rt == e
    assert rt.message_id == "msg_99"


def test_legacy_assistant_text_record_decodes_without_message_id():
    """Pre-slice-2 records didn't carry message_id; they must still
    decode cleanly."""
    legacy = {"t": "AssistantText", "text": "hi", "usage": None}
    ev = decode_event(legacy)
    assert isinstance(ev, AssistantText)
    assert ev.text == "hi"
    assert ev.message_id is None


def test_legacy_tool_result_record_decodes_with_defaults():
    legacy = {"t": "ToolResult", "text": "ok", "is_error": False}
    ev = decode_event(legacy)
    assert isinstance(ev, ToolResult)
    assert ev.tool_call_id is None
    assert ev.kind is None
