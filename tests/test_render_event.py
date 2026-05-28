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


def test_tool_use_kind_read_renders_book_icon():
    out = as_text(render_event(
        ToolUse(name="Read", summary="foo.py", kind="read"), C))
    assert "📖" in out
    assert "Read" in out


def test_tool_use_kind_execute_renders_terminal_icon():
    out = as_text(render_event(
        ToolUse(name="Bash", summary="echo hi", kind="execute"), C))
    assert "⌬" in out


def test_tool_use_kind_edit_renders_pencil():
    out = as_text(render_event(
        ToolUse(name="Edit", summary="x.py", kind="edit"), C))
    assert "✏" in out  # variation selector intentionally not asserted


def test_tool_use_kind_search_renders_magnifier():
    out = as_text(render_event(
        ToolUse(name="Grep", summary="foo", kind="search"), C))
    assert "🔎" in out


def test_tool_use_kind_think_renders_sparkle():
    out = as_text(render_event(
        ToolUse(name="Task", summary="plan", kind="think"), C))
    assert "✻" in out


def test_tool_use_no_kind_falls_back_to_dot():
    out = as_text(render_event(
        ToolUse(name="MysteryTool", summary="x"), C))
    assert "⏺" in out  # current behavior


def test_tool_use_unknown_kind_also_falls_back():
    out = as_text(render_event(
        ToolUse(name="X", summary="y", kind="bogus"), C))
    assert "⏺" in out


def test_tool_use_location_pathhint_shortens_long_path():
    out = as_text(render_event(
        ToolUse(name="Read", summary="",
                kind="read",
                locations=(("/very/deep/nested/path/foo.py", None),)), C))
    assert "foo.py" in out
    # No need to assert the absence of /very — the renderer might show
    # the full path; what matters is the tail is visible.


def test_tool_use_location_with_line_appended():
    out = as_text(render_event(
        ToolUse(name="Read", summary="",
                kind="read",
                locations=(("foo.py", 42),)), C))
    assert "foo.py" in out
    assert "42" in out


def test_tool_use_falls_back_to_summary_when_no_location():
    out = as_text(render_event(
        ToolUse(name="Bash", summary="echo hi", kind="execute"), C))
    assert "echo hi" in out


def test_thinking_content_shown():
    out = as_text(render_event(AssistantThinking("secret chain"), C))
    assert "secret chain" in out
    assert "✻" in out


def test_thinking_empty_falls_back_to_label():
    out = as_text(render_event(AssistantThinking(""), C))
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
