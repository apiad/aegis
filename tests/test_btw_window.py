"""The /btw window assembler — pure, no LLM, no disk.

Spec: docs/superpowers/specs/2026-07-31-aegis-btw-side-note-design.md
"""
from __future__ import annotations

import pytest

from aegis.btw.window import assemble
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, Result, SystemInit,
    ToolResult, ToolUse, Unknown, UserMessage,
)
from aegis.state.session_log import EventReplay


def replay(*events) -> EventReplay:
    return EventReplay(events=list(events), interrupted=False)


def turn(user: str, assistant: str) -> list:
    """One complete turn: the user speaks, the agent answers, Result ends it."""
    return [UserMessage(text=user), AssistantText(text=assistant),
            Result(duration_ms=100, is_error=False)]


# ---------- what goes in ------------------------------------------------

def test_user_and_assistant_text_go_in_verbatim():
    w = assemble(replay(*turn("why does resume take path A?", "because X")))
    assert "why does resume take path A?" in w.text
    assert "because X" in w.text


def test_speakers_are_distinguishable():
    """A window that cannot tell user from agent is the agent talking to
    itself — the exact thing UserMessage landed to fix."""
    w = assemble(replay(*turn("ask", "answer")))
    user_at = w.text.index("ask")
    agent_at = w.text.index("answer")
    assert w.text[:user_at].rstrip().endswith(("user:", "user"))
    assert w.text[:agent_at].rstrip().endswith(("assistant:", "assistant"))


def test_tool_use_renders_as_name_and_summary():
    w = assemble(replay(
        ToolUse(name="Read", summary="src/aegis/events.py"),
        Result(duration_ms=1, is_error=False)))
    assert "Read" in w.text and "src/aegis/events.py" in w.text


def test_tool_result_text_goes_in():
    w = assemble(replay(
        ToolUse(name="Bash", summary="ls"),
        ToolResult(text="a.py b.py", is_error=False),
        Result(duration_ms=1, is_error=False)))
    assert "a.py b.py" in w.text


def test_agent_plan_goes_in_compactly():
    w = assemble(replay(
        AgentPlan(entries=(PlanEntry(content="write the assembler",
                                     status="in_progress"),)),
        Result(duration_ms=1, is_error=False)))
    assert "write the assembler" in w.text


# ---------- what stays out ----------------------------------------------

def test_thinking_is_excluded():
    w = assemble(replay(
        AssistantThinking(text="a private deliberation"),
        AssistantText(text="the answer"),
        Result(duration_ms=1, is_error=False)))
    assert "a private deliberation" not in w.text
    assert "the answer" in w.text


@pytest.mark.parametrize("ev", [
    SystemInit(session_id="s1", model="opus"),
    Unknown(raw='{"type":"system","subtype":"thinking_tokens"}'),
])
def test_noise_events_are_excluded(ev):
    w = assemble(replay(ev, AssistantText(text="kept"),
                        Result(duration_ms=1, is_error=False)))
    assert "thinking_tokens" not in w.text
    assert "s1" not in w.text
    assert "kept" in w.text


# ---------- the turn boundary -------------------------------------------

def test_turn_boundary_keeps_the_last_n_turns():
    evs = []
    for i in range(20):
        evs += turn(f"question {i}", f"answer {i}")
    w = assemble(replay(*evs), max_turns=10)
    assert "question 19" in w.text
    assert "question 10" in w.text
    assert "question 9" not in w.text
    assert w.turns_included == 10
    assert w.turns_total == 20


def test_fewer_turns_available_than_requested():
    evs = turn("only", "turn")
    w = assemble(replay(*evs), max_turns=10)
    assert w.turns_included == 1
    assert w.turns_total == 1


def test_an_unterminated_final_turn_is_included():
    """/btw fires mid-turn. What has been flushed so far has no Result yet
    and is the most relevant thing in the window."""
    evs = turn("done", "answered") + [UserMessage(text="the live question"),
                                      ToolUse(name="Read", summary="f.py")]
    w = assemble(replay(*evs))
    assert "the live question" in w.text


# ---------- newest-first is an invariant, not a detail ------------------

def test_over_budget_keeps_the_last_turn_not_the_first():
    """Backwards, /btw confidently answers a question nobody asked."""
    evs = []
    for i in range(20):
        evs += turn(f"question {i}", "x" * 8000)
    w = assemble(replay(*evs), budget_tokens=4000)
    assert "question 19" in w.text
    assert "question 0" not in w.text


def test_events_stay_in_chronological_order():
    evs = turn("first", "1st") + turn("second", "2nd")
    w = assemble(replay(*evs))
    assert w.text.index("first") < w.text.index("second")


# ---------- per-item truncation -----------------------------------------

def test_a_huge_tool_result_is_truncated_and_marked():
    w = assemble(replay(
        ToolUse(name="Bash", summary="cat big.log"),
        ToolResult(text="x" * 200_000, is_error=False),
        Result(duration_ms=1, is_error=False)), item_chars=500)
    assert len(w.text) < 2_000
    assert "[+199,500 chars]" in w.text
    assert w.truncated == 1


def test_a_huge_tool_use_summary_is_truncated():
    """`_summarize_tool` falls through to the first string value for any
    tool outside `_TOOL_SUMMARY_KEY`, so a Task dispatch contributes its
    whole subagent prompt. Measured at 98,827 chars in one real window."""
    w = assemble(replay(
        ToolUse(name="Task", summary="y" * 100_000),
        Result(duration_ms=1, is_error=False)), item_chars=500)
    assert len(w.text) < 1_000
    assert w.truncated == 1


def test_an_error_result_is_marked_as_one():
    w = assemble(replay(
        ToolUse(name="Bash", summary="false"),
        ToolResult(text="boom", is_error=True),
        Result(duration_ms=1, is_error=False)))
    assert "result[error]: boom" in w.text


def test_a_single_item_larger_than_the_budget_still_produces_something():
    w = assemble(replay(AssistantText(text="z" * 500_000),
                        Result(duration_ms=1, is_error=False)),
                 budget_tokens=1_000)
    assert w.text
    assert w.approx_tokens <= 1_100
    assert "chars]" in w.text
    assert w.bound_by == "budget"


# ---------- the honest header -------------------------------------------

def test_header_reports_the_turns_it_dropped():
    evs = []
    for i in range(47):
        evs += turn(f"q{i}", f"a{i}")
    w = assemble(replay(*evs), max_turns=8)
    assert w.header == "last 8 of 47 turns"


def test_header_reports_truncated_items():
    w = assemble(replay(
        ToolUse(name="Bash", summary="cat a"),
        ToolResult(text="x" * 5_000, is_error=False),
        ToolUse(name="Bash", summary="cat b"),
        ToolResult(text="y" * 5_000, is_error=False),
        Result(duration_ms=1, is_error=False)), item_chars=500)
    assert w.header == "all 1 turn · 2 items truncated"


def test_header_says_all_when_nothing_was_dropped():
    evs = turn("q0", "a0") + turn("q1", "a1")
    w = assemble(replay(*evs))
    assert w.header == "all 2 turns"


# ---------- which bound binds -------------------------------------------

def test_bound_by_reports_the_turn_cap():
    evs = []
    for i in range(20):
        evs += turn(f"q{i}", f"a{i}")
    assert assemble(replay(*evs), max_turns=10).bound_by == "turns"


def test_bound_by_reports_the_budget():
    evs = []
    for i in range(20):
        evs += turn(f"q{i}", "x" * 8_000)
    assert assemble(replay(*evs), budget_tokens=4_000).bound_by == "budget"


def test_bound_by_reports_all_when_the_whole_log_fits():
    assert assemble(replay(*turn("q", "a"))).bound_by == "all"


# ---------- coalescing ---------------------------------------------------

def test_streamed_chunks_are_coalesced_into_one_line():
    """A persisted token stream is 116 AssistantText events, not one."""
    chunks = [AssistantText(text=t, message_id="m1")
              for t in ("the ", "answer ", "in ", "pieces")]
    w = assemble(replay(*chunks, Result(duration_ms=1, is_error=False)))
    assert "assistant: the answer in pieces" in w.text
    assert w.text.count("assistant:") == 1


# ---------- degenerate input --------------------------------------------

def test_an_empty_log_yields_an_empty_window_not_a_crash():
    w = assemble(replay())
    assert w.text == ""
    assert w.turns_total == 0
    assert w.turns_included == 0


def test_a_log_of_pure_noise_yields_an_empty_window():
    w = assemble(replay(SystemInit(session_id="s"),
                        AssistantThinking(text="redacted anyway")))
    assert w.text == ""
