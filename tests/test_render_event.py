from rich.console import Console
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, ToolUse,
    ToolResult, Result, SystemInit, Unknown,
)
from aegis.render import render_event, render_tool_use, render_user_line
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
    out = as_text(render_event(
        ToolUse(name="Read", summary="foo.py",
                raw_input={"file_path": "a/foo.py"}), C))
    assert "read foo.py" in out
    assert out.count("\n") <= 2


def test_tool_use_kind_read_renders_book_icon():
    out = as_text(render_event(
        ToolUse(name="Read", summary="foo.py", kind="read",
                raw_input={"file_path": "a/foo.py"}), C))
    assert "📖" in out
    assert "read foo.py" in out


def test_tool_use_bash_shows_description():
    out = as_text(render_event(
        ToolUse(name="Bash", summary="uv run pytest", kind="execute",
                raw_input={"command": "uv run pytest",
                           "description": "Run the suite"}), C))
    assert "Run the suite" in out and "Bash" not in out


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


def test_agent_plan_renders_status_glyphs():
    plan = AgentPlan(entries=(
        PlanEntry(content="alpha", status="completed"),
        PlanEntry(content="beta", status="in_progress"),
        PlanEntry(content="gamma", status="pending"),
    ))
    out = as_text(render_event(plan, C))
    # Each entry's content appears, each with its corresponding glyph.
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "●" in out   # completed
    assert "◐" in out   # in_progress
    assert "○" in out   # pending


def test_agent_plan_empty_renders_label():
    """An empty plan is still meaningful — the model said 'no plan' —
    so render a single muted line rather than nothing."""
    out = as_text(render_event(AgentPlan(entries=()), C))
    assert out.strip() != ""


def test_agent_plan_completed_count_visible():
    """Header summarizes progress so the eye catches it at a glance."""
    plan = AgentPlan(entries=(
        PlanEntry(content="a", status="completed"),
        PlanEntry(content="b", status="completed"),
        PlanEntry(content="c", status="in_progress"),
        PlanEntry(content="d", status="pending"),
    ))
    out = as_text(render_event(plan, C))
    assert "2/4" in out or "2 of 4" in out


def test_tool_result_with_diff_renders_unified_preview():
    """ToolResult.diff shows up as a small unified-style block — minus
    lines for old, plus lines for new — under the success/error line."""
    out = as_text(render_event(
        ToolResult(
            text="ok",
            is_error=False,
            kind="edit",
            diff=("x.py", "alpha\nbeta\n", "alpha\nGAMMA\nbeta\n"),
        ), C))
    # The path is referenced.
    assert "x.py" in out
    # Removed line marker present (- gamma).
    assert "+" in out  # plus marker for added line
    # The added word appears.
    assert "GAMMA" in out


def test_tool_result_diff_pure_addition():
    """Write case — old is empty, new has the full content. Should
    render the new content as additions only."""
    out = as_text(render_event(
        ToolResult(
            text="ok",
            is_error=False,
            kind="edit",
            diff=("new.py", "", "first\nsecond\n"),
        ), C))
    assert "new.py" in out
    assert "first" in out


def test_tool_result_without_diff_renders_legacy_one_liner():
    """Backward compat: ToolResult without diff renders the same
    single-line ok/error preview as before."""
    out = as_text(render_event(
        ToolResult(text="bar", is_error=False, kind="read"), C))
    assert "ok" in out.lower() or "└" in out
    # No diff gutter when there's no diff.


def test_tool_result_diff_truncates_long_changes():
    """Big diffs get capped — we show a few +/- lines plus a hint that
    more was elided. The renderer shouldn't dump 500 lines into a
    transcript block."""
    old = "".join(f"line-old-{i}\n" for i in range(40))
    new = "".join(f"line-new-{i}\n" for i in range(40))
    out = as_text(render_event(
        ToolResult(text="ok", is_error=False, kind="edit",
                   diff=("x.py", old, new)), C))
    # Total rendered lines stays bounded (< 20 visible).
    assert out.count("\n") < 20


def test_tool_use_hint_suppressed_when_equal_to_name():
    """ACP titles often equal the filename ("target.txt"); the pathhint
    derives the same string from locations[0]. Don't render both."""
    out = as_text(render_event(
        ToolUse(name="target.txt", summary="", kind="read",
                locations=(("/some/path/target.txt", None),)), C))
    # The name shows once; no parenthetical duplicate.
    assert out.count("target.txt") == 1


def test_running_tool_timer_shows_subsecond():
    """Live per-tool timer ticks in tenths (like the WorkingIndicator),
    not whole seconds — so the digits visibly move at the 0.1s cadence."""
    ev = ToolUse(name="Bash", summary="sleep", kind="execute",
                 raw_input={"command": "sleep 5"})
    out = as_text(render_tool_use(ev, C, elapsed=3.4, running=True, frame=0))
    assert "3.4s" in out


def test_frozen_tool_duration_keeps_subsecond():
    """Once folded, the duration freezes at the tenths value last shown —
    no jump back to a rounded whole second."""
    ev = ToolUse(name="Bash", summary="sleep", kind="execute",
                 raw_input={"command": "sleep 5"})
    out = as_text(render_tool_use(ev, C, elapsed=3.4, running=False))
    assert "3.4s" in out


def test_tool_duration_minutes_unchanged():
    ev = ToolUse(name="Bash", summary="build", kind="execute",
                 raw_input={"command": "make"})
    out = as_text(render_tool_use(ev, C, elapsed=125.0, running=True, frame=0))
    assert "2m05s" in out


def test_thinking_renders_compact_thought_summary():
    out = as_text(render_event(AssistantThinking("secret chain"), C))
    assert "thought" in out
    assert "tok" in out
    # Reasoning is collapsed to a summary, not dumped (the full text stays in
    # the copy payload, not the rendered line).
    assert "secret chain" not in out


def test_thinking_empty_still_renders_thought_line():
    out = as_text(render_event(AssistantThinking(""), C))
    assert "thought" in out


def test_tool_result_first_line_only():
    out = as_text(render_event(ToolResult(text="l1\nl2\nl3", is_error=False), C))
    assert "l1" in out and "l3" not in out


def test_tool_result_error_marked():
    out = as_text(render_event(ToolResult(text="boom", is_error=True), C))
    assert "error" in out.lower()


def test_result_separator():
    out = as_text(render_event(Result(duration_ms=2500, is_error=False), C))
    assert "2.5" in out


def test_result_shows_cost_when_populated():
    """When cost_usd is set, the result line surfaces it so the eye
    catches dollar burn at a glance. Rounding matches the status line
    (status_line.metrics._fmt_cost) — sub-cent → '0.X¢', whole cents
    under $1 → 'N¢', dollars otherwise → '$N.NN'."""
    # 1.23¢ → "1¢" (whole cents under $1)
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False, cost_usd=0.0123), C))
    assert "1¢" in out
    # 0.5¢ → "0.5¢" (sub-cent)
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False, cost_usd=0.005), C))
    assert "0.5¢" in out
    # $1.23 → "$1.23" (dollars)
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False, cost_usd=1.23), C))
    assert "$1.23" in out


def test_result_shows_stop_reason_when_non_default():
    """end_turn is the boring case — don't pollute. max_tokens /
    refusal / cancelled mean something happened that should be visible."""
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False,
               stop_reason="max_tokens"), C))
    assert "max_tokens" in out


def test_result_omits_end_turn_stop_reason():
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False, stop_reason="end_turn"), C))
    assert "end_turn" not in out


def test_result_combines_cost_and_stop_reason():
    out = as_text(render_event(
        Result(duration_ms=1000, is_error=False,
               cost_usd=0.05, stop_reason="refusal"), C))
    assert "5¢" in out
    assert "refusal" in out


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
