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
