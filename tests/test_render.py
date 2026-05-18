from rich.console import Console
from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown,
)
from aegis.render import Renderer


def render_one(ev) -> str:
    con = Console(record=True, width=80)
    Renderer(con).render(ev)
    return con.export_text()


def test_assistant_text_rendered():
    assert "hello world" in render_one(AssistantText("hello world"))


def test_tool_use_one_liner():
    out = render_one(ToolUse(name="Read", summary="foo.py"))
    assert "Read" in out and "foo.py" in out
    assert out.count("\n") <= 2


def test_thinking_is_collapsed():
    out = render_one(AssistantThinking("a very long secret reasoning chain"))
    assert "secret reasoning" not in out
    assert "Thinking" in out


def test_tool_result_ok_collapsed():
    out = render_one(ToolResult(text="line1\nline2\nline3", is_error=False))
    assert "line1" in out
    assert "line3" not in out


def test_tool_result_error_marked():
    out = render_one(ToolResult(text="boom", is_error=True))
    assert "error" in out.lower()


def test_systeminit_and_unknown_render_nothing():
    assert render_one(SystemInit(session_id="x")).strip() == ""
    assert render_one(Unknown(raw="{}")).strip() == ""


def test_result_shows_separator():
    out = render_one(Result(duration_ms=2500, is_error=False))
    assert "2.5" in out or "2500" in out
