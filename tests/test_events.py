import json
from pathlib import Path
import pytest
from aegis.events import (
    parse, SystemInit, AssistantText, AssistantThinking,
    ToolUse, ToolResult, Result, Unknown,
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
