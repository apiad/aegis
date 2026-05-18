from rich.console import Console
from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown,
)
from aegis.render import render_event, render_user_line
from aegis.tui.themes import aegis_colors, INK

C = aegis_colors(INK)


def as_text(renderable) -> str:
    con = Console(record=True, width=80)
    con.print(renderable)
    return con.export_text()


def test_assistant_text_is_renderable():
    out = as_text(render_event(AssistantText("hello world"), C))
    assert "hello world" in out


def test_tool_use_one_liner():
    out = as_text(render_event(ToolUse(name="Read", summary="foo.py"), C))
    assert "Read" in out and "foo.py" in out
    assert out.count("\n") <= 2


def test_thinking_collapsed():
    out = as_text(render_event(AssistantThinking("secret chain"), C))
    assert "secret chain" not in out
    assert "Thinking" in out


def test_tool_result_first_line_only():
    out = as_text(render_event(ToolResult(text="l1\nl2\nl3", is_error=False), C))
    assert "l1" in out and "l3" not in out


def test_tool_result_error_marked():
    out = as_text(render_event(ToolResult(text="boom", is_error=True), C))
    assert "error" in out.lower()


def test_result_separator():
    out = as_text(render_event(Result(duration_ms=2500, is_error=False), C))
    assert "2.5" in out


def test_systeminit_and_unknown_are_none():
    assert render_event(SystemInit(session_id="x"), C) is None
    assert render_event(Unknown(raw="{}"), C) is None


def test_render_user_line_has_accent_prefix_and_bg():
    line = render_user_line("hello", C, width=40)
    plain = line.plain
    assert plain.startswith("› hello")
    assert len(plain) == 40                      # padded to full width band
    # the line's base style carries the lighter user background
    assert C.user_bg.lstrip("#").lower() in str(line.style).lower()


def test_render_user_line_no_width_not_padded():
    line = render_user_line("hi", C)
    assert line.plain == "› hi"                   # no width → no pad
