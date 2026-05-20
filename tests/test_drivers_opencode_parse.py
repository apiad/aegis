"""OpenCode JSON event parser tests.

OpenCode emits line-delimited JSON when invoked with `--format json`.
Event shapes observed against opencode v1.x:

    {"type":"step_start","part":{"id":"...","sessionID":"...","type":"step-start"}}
    {"type":"text","part":{"text":"...","time":{...},"type":"text"}}
    {"type":"tool","part":{"name":"...","args":{...},"type":"tool"}}
    {"type":"step_finish","part":{"reason":"stop","tokens":{...},"cost":...}}
    {"type":"error","error":{"name":"...","data":{"message":"..."}}}
"""
from __future__ import annotations

from aegis.drivers.opencode_parse import parse
from aegis.events import (
    AssistantText, Result, SystemInit, ToolUse, Unknown,
)


def test_step_start_yields_system_init():
    line = (
        '{"type":"step_start","sessionID":"ses_abc",'
        '"part":{"id":"prt_1","sessionID":"ses_abc","type":"step-start"}}'
    )
    ev = parse(line)
    assert isinstance(ev, SystemInit)
    assert ev.session_id == "ses_abc"


def test_text_part_yields_assistant_text():
    line = (
        '{"type":"text","sessionID":"ses_abc",'
        '"part":{"text":"Hello","type":"text",'
        '"time":{"start":1,"end":2}}}'
    )
    ev = parse(line)
    assert isinstance(ev, AssistantText)
    assert ev.text == "Hello"


def test_step_finish_yields_result_success():
    line = (
        '{"type":"step_finish","sessionID":"ses_abc",'
        '"part":{"reason":"stop","type":"step-finish",'
        '"tokens":{"total":11335,"input":10672,"output":23},'
        '"cost":0.0032}}'
    )
    ev = parse(line)
    assert isinstance(ev, Result)
    assert ev.is_error is False
    assert ev.input_tokens == 10672
    assert ev.output_tokens == 23


def test_step_finish_with_error_reason_yields_is_error():
    line = (
        '{"type":"step_finish","sessionID":"ses_abc",'
        '"part":{"reason":"error","type":"step-finish",'
        '"tokens":{"total":100,"input":50,"output":10}}}'
    )
    ev = parse(line)
    assert isinstance(ev, Result)
    assert ev.is_error is True


def test_error_event_yields_result_is_error():
    """OpenCode error events terminate the stream; surface as a failed
    Result so the session loop ends cleanly."""
    line = (
        '{"type":"error","sessionID":"ses_abc",'
        '"error":{"name":"UnknownError",'
        '"data":{"message":"Model not found: foo/bar."}}}'
    )
    ev = parse(line)
    assert isinstance(ev, Result)
    assert ev.is_error is True


def test_tool_part_yields_tool_use():
    line = (
        '{"type":"tool","sessionID":"ses_abc",'
        '"part":{"name":"read","args":{"path":"/etc/hosts"},'
        '"type":"tool"}}'
    )
    ev = parse(line)
    assert isinstance(ev, ToolUse)
    assert ev.name == "read"
    assert "/etc/hosts" in ev.summary


def test_malformed_json_returns_unknown():
    ev = parse('not json {')
    assert isinstance(ev, Unknown)


def test_unknown_type_returns_unknown():
    ev = parse('{"type":"telemetry","foo":"bar"}')
    assert isinstance(ev, Unknown)
