from rich.console import Console
from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown,
)
from aegis.render import render_event


def as_text(renderable) -> str:
    con = Console(record=True, width=80)
    con.print(renderable)
    return con.export_text()


def test_assistant_text_is_renderable():
    out = as_text(render_event(AssistantText("hello world")))
    assert "hello world" in out


def test_tool_use_one_liner():
    out = as_text(render_event(ToolUse(name="Read", summary="foo.py")))
    assert "Read" in out and "foo.py" in out
    assert out.count("\n") <= 2


def test_thinking_collapsed():
    out = as_text(render_event(AssistantThinking("secret chain")))
    assert "secret chain" not in out
    assert "Thinking" in out


def test_tool_result_first_line_only():
    out = as_text(render_event(ToolResult(text="l1\nl2\nl3", is_error=False)))
    assert "l1" in out and "l3" not in out


def test_tool_result_error_marked():
    out = as_text(render_event(ToolResult(text="boom", is_error=True)))
    assert "error" in out.lower()


def test_result_separator():
    out = as_text(render_event(Result(duration_ms=2500, is_error=False)))
    assert "2.5" in out


def test_systeminit_and_unknown_are_none():
    assert render_event(SystemInit(session_id="x")) is None
    assert render_event(Unknown(raw="{}")) is None
