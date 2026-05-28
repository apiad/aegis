import json
from pathlib import Path
import pytest
from aegis.events import (
    parse, SystemInit, AssistantText, AssistantThinking,
    ToolUse, ToolResult, Result, Unknown, ParserState,
)

FIX = Path(__file__).parent / "fixtures"


def test_unknown_never_raises():
    assert isinstance(parse('not json at all'), Unknown)
    assert isinstance(parse('{"type":"totally_new_event"}'), Unknown)
    assert isinstance(parse(''), Unknown)


def test_parse_system_init():
    ev = parse(json.dumps({"type": "system", "subtype": "init",
                            "session_id": "abc"}))
    assert isinstance(ev, SystemInit)
    assert ev.session_id == "abc"


def test_parse_assistant_text():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello"}]},
    }))
    assert isinstance(ev, AssistantText)
    assert ev.text == "hello"


def test_assistant_text_message_id_default_none():
    t = AssistantText(text="hi")
    assert t.message_id is None


def test_assistant_text_carries_message_id():
    t = AssistantText(text="hi", message_id="msg_42")
    assert t.message_id == "msg_42"


def test_assistant_thinking_carries_message_id():
    t = AssistantThinking(text="hmm", message_id="msg_99")
    assert t.message_id == "msg_99"


def test_parse_assistant_text_populates_message_id_from_claude_stream():
    """Claude's assistant.message.id is the natural aggregation key
    for streaming text chunks."""
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01ABC",
            "content": [{"type": "text", "text": "hello"}],
        },
    }))
    assert isinstance(ev, AssistantText)
    assert ev.message_id == "msg_01ABC"


def test_parse_assistant_thinking_populates_message_id():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01XYZ",
            "content": [{"type": "thinking", "thinking": "..."}],
        },
    }))
    assert isinstance(ev, AssistantThinking)
    assert ev.message_id == "msg_01XYZ"


def test_parse_assistant_text_without_message_id_stays_none():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi"}]},
    }))
    assert ev.message_id is None


def test_parse_assistant_thinking():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "thinking",
                                  "thinking": "hmm"}]},
    }))
    assert isinstance(ev, AssistantThinking)


def test_parse_tool_use():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "echo hi"}}]},
    }))
    assert isinstance(ev, ToolUse)
    assert ev.name == "Bash"
    assert ev.summary == "echo hi"


def test_tool_use_optional_fields_default():
    """ToolUse(name=…, summary=…) (the legacy two-arg form) must still
    construct cleanly with sensible defaults on all new fields.
    Drivers populate the new fields opportunistically; the renderer
    treats absence as "fall back to the LCD view"."""
    t = ToolUse(name="X", summary="")
    assert t.kind is None
    assert t.tool_call_id is None
    assert t.raw_input is None
    assert t.locations == ()
    assert t.status is None


def test_tool_use_carries_kind_locations_raw_input_tool_call_id():
    t = ToolUse(name="Read", summary="foo.py",
                kind="read", tool_call_id="toolu_1",
                raw_input={"file_path": "foo.py"},
                locations=(("foo.py", 12),),
                status="in_progress")
    assert t.kind == "read"
    assert t.tool_call_id == "toolu_1"
    assert t.raw_input == {"file_path": "foo.py"}
    assert t.locations == (("foo.py", 12),)
    assert t.status == "in_progress"


def test_tool_result_optional_fields_default():
    r = ToolResult(text="ok", is_error=False)
    assert r.tool_call_id is None
    assert r.kind is None


def test_tool_result_carries_tool_call_id_and_kind():
    r = ToolResult(text="ok", is_error=False,
                   tool_call_id="toolu_1", kind="read")
    assert r.tool_call_id == "toolu_1"
    assert r.kind == "read"


@pytest.mark.parametrize("name,expected", [
    ("Read", "read"),
    ("Bash", "execute"),
    ("BashOutput", "execute"),
    ("KillShell", "execute"),
    ("Edit", "edit"),
    ("Write", "edit"),
    ("NotebookEdit", "edit"),
    ("Glob", "search"),
    ("Grep", "search"),
    ("WebFetch", "fetch"),
    ("WebSearch", "fetch"),
    ("Task", "think"),
    ("Agent", "think"),
    ("MyMcpTool", "other"),  # unknown → other
])
def test_tool_use_kind_derived_from_name(name, expected):
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "toolu_x", "name": name,
            "input": {},
        }]},
    }))
    assert isinstance(ev, ToolUse)
    assert ev.kind == expected


def test_tool_use_carries_raw_input_and_tool_call_id():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "toolu_42", "name": "Read",
            "input": {"file_path": "src/foo.py"},
        }]},
    }))
    assert ev.tool_call_id == "toolu_42"
    assert ev.raw_input == {"file_path": "src/foo.py"}


def test_tool_use_locations_from_file_path():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "tu", "name": "Read",
            "input": {"file_path": "src/foo.py"},
        }]},
    }))
    assert ev.locations == (("src/foo.py", None),)


def test_tool_use_locations_empty_when_no_file_path():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "tu", "name": "Bash",
            "input": {"command": "ls"},
        }]},
    }))
    assert ev.locations == ()


def test_tool_result_kind_correlated_via_parser_state():
    state = ParserState()
    use = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "toolu_99", "name": "Edit",
            "input": {"file_path": "x.py", "old_string": "a",
                      "new_string": "b"},
        }]},
    }), state=state)
    assert isinstance(use, ToolUse)
    assert use.kind == "edit"

    result = parse(json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "toolu_99",
            "content": "ok", "is_error": False,
        }]},
    }), state=state)
    assert isinstance(result, ToolResult)
    assert result.tool_call_id == "toolu_99"
    assert result.kind == "edit"


def test_tool_result_kind_none_without_matching_state():
    """If parser state never saw the matching tool_use (e.g. truncated
    stream replay), tool_call_id passes through but kind is None."""
    result = parse(json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "missing",
            "content": "ok", "is_error": False,
        }]},
    }))
    assert isinstance(result, ToolResult)
    assert result.tool_call_id == "missing"
    assert result.kind is None


def test_parse_tool_result():
    ev = parse(json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result",
                                  "content": "ok output",
                                  "is_error": False}]},
    }))
    assert isinstance(ev, ToolResult)
    assert ev.is_error is False
    assert "ok output" in ev.text


def test_parse_result():
    ev = parse(json.dumps({"type": "result", "subtype": "success",
                            "duration_ms": 1234, "is_error": False}))
    assert isinstance(ev, Result)
    assert ev.duration_ms == 1234


@pytest.mark.parametrize("fixture",
                         ["stream_text.jsonl", "stream_tool.jsonl"])
def test_real_fixture_lines_all_parse(fixture):
    lines = [l for l in (FIX / fixture).read_text().splitlines() if l.strip()]
    assert lines, f"{fixture} is empty - rerun scripts/capture_fixtures.sh"
    events = [parse(l) for l in lines]
    assert any(isinstance(e, Result) for e in events)
    assert any(isinstance(e, (AssistantText, ToolUse)) for e in events)


def test_parse_result_with_usage_tokens():
    ev = parse(json.dumps({
        "type": "result", "subtype": "success",
        "duration_ms": 700, "is_error": False,
        "usage": {"input_tokens": 1200, "output_tokens": 340},
    }))
    assert isinstance(ev, Result)
    assert ev.input_tokens == 1200
    assert ev.output_tokens == 340


def test_parse_result_without_usage_tokens_are_none():
    ev = parse(json.dumps({"type": "result", "subtype": "success",
                            "duration_ms": 1, "is_error": False}))
    assert ev.input_tokens is None
    assert ev.output_tokens is None


def test_token_usage_true_input_and_cached_pct():
    from aegis.events import TokenUsage
    u = TokenUsage(input=4, cache_creation=15000, cache_read=85000, output=120)
    assert u.true_input == 100004              # input + cc + cr
    assert u.cached_pct == round(100 * 85000 / 100004)
    z = TokenUsage(input=0, cache_creation=0, cache_read=0, output=0)
    assert z.true_input == 0 and z.cached_pct == 0


def test_parse_result_carries_token_usage():
    ev = parse(json.dumps({
        "type": "result", "subtype": "success",
        "duration_ms": 9, "is_error": False,
        "usage": {"input_tokens": 4, "cache_creation_input_tokens": 1192,
                  "cache_read_input_tokens": 37801, "output_tokens": 261},
    }))
    assert isinstance(ev, Result)
    assert ev.usage is not None
    assert ev.usage.true_input == 4 + 1192 + 37801
    assert ev.usage.output == 261
    assert ev.input_tokens == 4 and ev.output_tokens == 261   # back-compat


def test_parse_assistant_text_carries_usage():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 2, "cache_creation_input_tokens": 37801,
                      "cache_read_input_tokens": 0, "output_tokens": 34},
        },
    }))
    assert isinstance(ev, AssistantText) and ev.text == "hi"
    assert ev.usage is not None and ev.usage.true_input == 2 + 37801


def test_parse_assistant_without_usage_is_none():
    ev = parse(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "x"}]},
    }))
    assert isinstance(ev, AssistantText)
    assert ev.usage is None
