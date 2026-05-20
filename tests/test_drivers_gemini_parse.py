"""Gemini CLI stream-json event parser tests.

Gemini emits line-delimited JSON when invoked with
`--output-format stream-json`. Event shapes (observed empirically):

    {"type": "init", "session_id": "...", "model": "..."}
    {"type": "message", "role": "user", "content": "..."}
    {"type": "message", "role": "assistant", "content": "...", "delta": true}
    {"type": "result", "status": "success", "stats": {...}}

We map these into aegis's canonical Event types so downstream rendering
+ metrics + queue/workflow capture all work uniformly across providers.
"""
from __future__ import annotations

from aegis.drivers.gemini_parse import parse
from aegis.events import (
    AssistantText, Result, SystemInit, ToolUse, Unknown,
)


def test_init_event_yields_system_init():
    line = '{"type":"init","timestamp":"2026-05-20T20:12:31Z","session_id":"abc-123","model":"gemini-3"}'
    ev = parse(line)
    assert isinstance(ev, SystemInit)
    assert ev.session_id == "abc-123"


def test_assistant_message_yields_assistant_text():
    line = '{"type":"message","timestamp":"2026-05-20T20:12:36Z","role":"assistant","content":"Hello.","delta":true}'
    ev = parse(line)
    assert isinstance(ev, AssistantText)
    assert ev.text == "Hello."


def test_user_message_is_ignored_as_echo():
    """User messages are the substrate's own input echoed back — not events
    the aegis layer cares about. Parser returns Unknown so the caller can
    filter."""
    line = '{"type":"message","role":"user","content":"say hello"}'
    ev = parse(line)
    assert isinstance(ev, Unknown)


def test_result_success_yields_result_not_error():
    line = (
        '{"type":"result","status":"success",'
        '"stats":{"total_tokens":21685,"input_tokens":21468,'
        '"output_tokens":32,"duration_ms":4680,"tool_calls":0}}'
    )
    ev = parse(line)
    assert isinstance(ev, Result)
    assert ev.is_error is False
    assert ev.duration_ms == 4680
    assert ev.input_tokens == 21468
    assert ev.output_tokens == 32


def test_result_failure_yields_is_error_true():
    line = '{"type":"result","status":"error","stats":{}}'
    ev = parse(line)
    assert isinstance(ev, Result)
    assert ev.is_error is True


def test_tool_call_event_yields_tool_use():
    """When Gemini reports a tool invocation (function call), surface
    a ToolUse so the renderer + metrics treat it uniformly."""
    line = (
        '{"type":"tool_call","name":"read_file",'
        '"args":{"path":"/etc/hosts"}}'
    )
    ev = parse(line)
    assert isinstance(ev, ToolUse)
    assert ev.name == "read_file"
    assert "/etc/hosts" in ev.summary


def test_malformed_json_returns_unknown():
    ev = parse('not json at all {')
    assert isinstance(ev, Unknown)
    assert ev.raw == 'not json at all {'


def test_unknown_type_returns_unknown():
    line = '{"type":"telemetry","foo":"bar"}'
    ev = parse(line)
    assert isinstance(ev, Unknown)
