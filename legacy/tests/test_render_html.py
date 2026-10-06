from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, Result,
    SystemInit, ToolResult, ToolUse, Unknown,
)
from aegis.render_html import render_event_html


def test_assistant_text_wrapped_and_escaped():
    h = render_event_html(AssistantText("hello world"))
    assert "hello world" in h
    assert "assistant-text" in h


def test_assistant_text_empty_is_none():
    assert render_event_html(AssistantText("   ")) is None


def test_html_escaping_neutralizes_markup():
    h = render_event_html(AssistantText("<script>alert(1)</script>"))
    assert "<script>" not in h
    assert "&lt;script&gt;" in h


def test_tool_use_read_icon_name_and_class():
    h = render_event_html(ToolUse(name="Read", summary="foo.py", kind="read",
                                  raw_input={"file_path": "a/foo.py"}))
    assert "📖" in h
    assert "read foo.py" in h
    assert "tool-use" in h
    assert "tool-desc" in h


def test_tool_use_hint_suppressed_when_equal_to_name():
    h = render_event_html(ToolUse(name="target.txt", summary="", kind="read",
                                  locations=(("/p/target.txt", None),)))
    assert h.count("target.txt") == 1


def test_tool_use_unknown_kind_falls_back_to_dot():
    h = render_event_html(ToolUse(name="X", summary="y"))
    assert "⏺" in h


def test_tool_result_ok_one_liner():
    h = render_event_html(ToolResult(text="bar", is_error=False, kind="read"))
    assert "tool-result" in h
    assert "ok" in h


def test_tool_result_error_marked():
    h = render_event_html(ToolResult(text="boom", is_error=True))
    assert "error" in h


def test_tool_result_diff_preview():
    h = render_event_html(ToolResult(
        text="ok", is_error=False, kind="edit",
        diff=("x.py", "alpha\nbeta\n", "alpha\nGAMMA\nbeta\n")))
    assert "x.py" in h
    assert "GAMMA" in h
    assert "+" in h


def test_agent_plan_glyphs_and_content():
    h = render_event_html(AgentPlan(entries=(
        PlanEntry(content="alpha", status="completed"),
        PlanEntry(content="beta", status="in_progress"),
        PlanEntry(content="gamma", status="pending"),
    )))
    assert "alpha" in h and "beta" in h and "gamma" in h
    assert "●" in h and "◐" in h and "○" in h
    assert "Plan" in h


def test_agent_plan_empty_label():
    h = render_event_html(AgentPlan(entries=()))
    assert "no plan" in h


def test_result_separator_with_cost():
    h = render_event_html(Result(duration_ms=2500, is_error=False,
                                 cost_usd=0.05))
    assert "2.5" in h
    assert "5¢" in h
    assert "result-sep" in h


def test_thinking_shows_content():
    h = render_event_html(AssistantThinking("secret chain"))
    assert "secret chain" in h
    assert "✻" in h


def test_thinking_empty_label():
    h = render_event_html(AssistantThinking(""))
    assert "Thinking" in h


def test_thinking_shows_token_estimate():
    # Claude redacts the text but reports the estimate — parity with TUI.
    h = render_event_html(AssistantThinking("", token_estimate=6050))
    assert "~6k tok" in h


def test_systeminit_and_unknown_are_none():
    assert render_event_html(SystemInit(session_id="x")) is None
    assert render_event_html(Unknown(raw="{}")) is None
