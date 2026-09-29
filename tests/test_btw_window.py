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


IMAGE_RESULT = (
    '[{"type": "image", "source": {"type": "base64", "data": "iVBORw0KGgoAAAANSUhEUg'
    + "A" * 4000 + '"}}]'
)


def test_an_image_result_is_summarised_not_prefixed():
    """A 200-char prefix of base64 is not a summary, it is a wasted slot.

    Measured on a real `/spawn` tail: three `Read(*.png)` results, each
    clipped to 200 chars of `iVBORw0KGgo...`, spent the whole window and left
    the operator's own words outside it.
    """
    w = assemble(replay(ToolResult(text=IMAGE_RESULT, is_error=False)))
    assert "iVBORw0KGgo" not in w.text
    assert "base64" not in w.text
    assert "image" in w.text


def test_an_image_result_still_says_it_happened():
    """Dropping the line entirely would read as a call that returned
    nothing. The agent has to know an image came back, and roughly how big."""
    w = assemble(replay(ToolResult(text=IMAGE_RESULT, is_error=False)))
    assert w.text.startswith("result:")
    assert len(w.text) < 120


def test_an_image_result_leaves_room_for_the_prose_around_it():
    """The regression this guards: the window is spent on image payloads and
    the turn's own user line never gets in."""
    events = [UserMessage(text="the referent lives here")]
    for _ in range(40):
        events.append(ToolResult(text=IMAGE_RESULT, is_error=False))
    w = assemble(replay(*events), max_turns=3, budget_tokens=2_000, item_chars=200)
    assert "the referent lives here" in w.text


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

# ---------- prose keeps its seat ---------------------------------------

def _tool_heavy_turn(user: str, assistant: str, calls: int = 40) -> list:
    """The shape every real agent turn has: the operator speaks once, then
    dozens of tool calls, then a little prose."""
    events: list = [UserMessage(text=user)]
    for i in range(calls):
        events.append(ToolUse(name="Bash", summary=f"grep -rn thing{i} src/ " + "x" * 300))
        events.append(ToolResult(text=f"hit {i}: " + "y" * 400, is_error=False))
    events.append(AssistantText(text=assistant))
    events.append(Result(duration_ms=100, is_error=False))
    return events


def test_the_operators_words_outrank_the_newest_tool_calls():
    """Filling purely backwards spends the budget on the last dozen tool
    calls, and the `user:` line that opened the turn is never reached.

    Measured over 71 real `/spawn` preambles: 30 of them (42%) carried not one
    `user:` line. The referent for "verify this test" lives in the prose.
    """
    w = assemble(
        replay(*_tool_heavy_turn("verify the resume path, not the retry path", "done")),
        max_turns=3, budget_tokens=2_000, item_chars=200,
    )
    assert "verify the resume path" in w.text


def test_the_agents_prose_outranks_the_newest_tool_calls():
    w = assemble(
        replay(*_tool_heavy_turn("go", "I found it: assemble fills backwards")),
        max_turns=3, budget_tokens=2_000, item_chars=200,
    )
    assert "assemble fills backwards" in w.text


def test_a_long_agent_answer_does_not_starve_the_operators_line():
    """The operator's words outrank the agent's own, and a reserved share
    spent newest-first does not deliver that on its own.

    Found by replaying 601 real September windows: a ~900-token closing
    `assistant:` block filled the prose share and the 293-token `user:` line
    behind it was rejected at 1231 of a 1200 ceiling — with 769 tokens of the
    2000 budget still unspent. Seven logs lost their `user:` line that way.
    """
    # The real proportions: a 293-token user line behind a 938-token answer,
    # 1,231 against a 1,200 ceiling, inside a 2,000 budget.
    events = [
        UserMessage(text="You are reviewing Task 2 of the recap schema. " + "ctx " * 285),
        AssistantText(text="### Spec Compliance\n\n" + "verdict prose. " * 250),
        Result(duration_ms=100, is_error=False),
    ]
    for i in range(30):
        events.append(ToolResult(text=f"hit {i}: " + "y" * 400, is_error=False))
    w = assemble(replay(*events), max_turns=3, budget_tokens=2_000, item_chars=200)
    assert "You are reviewing Task 2" in w.text


def test_the_operators_line_wins_even_when_it_alone_exceeds_the_prose_share():
    """One real case had a single 1,396-token `user:` message against a
    1,200-token prose share. Capping prose there loses the only thing in the
    window that carries the referent."""
    events = [
        UserMessage(text="the whole brief, verbatim. " + "detail " * 900),
        AssistantText(text="on it"),
        Result(duration_ms=100, is_error=False),
    ]
    for i in range(30):
        events.append(ToolResult(text=f"hit {i}: " + "y" * 400, is_error=False))
    w = assemble(replay(*events), max_turns=3, budget_tokens=2_000, item_chars=200)
    assert "the whole brief, verbatim" in w.text


def test_prose_does_not_evict_the_tool_calls_entirely():
    """Prose gets a reserved share, not the whole window. A turn is mostly
    tool calls and they are how the agent shows what it actually did."""
    w = assemble(
        replay(*_tool_heavy_turn("go", "done")),
        max_turns=3, budget_tokens=2_000, item_chars=200,
    )
    assert any(ln.startswith(("tool:", "result")) for ln in w.text.splitlines())


def test_prose_first_does_not_widen_the_window():
    """The fix is a fill order, not a budget increase. It must cost the same."""
    events = _tool_heavy_turn("go", "done")
    w = assemble(replay(*events), max_turns=3, budget_tokens=2_000, item_chars=200)
    assert w.approx_tokens <= 2_000


def test_reordering_the_fill_keeps_chronological_order():
    """Prose may jump the queue for a *seat*, never for a position: a window
    that reads out of order is a window that invents a causality."""
    events = _tool_heavy_turn("the first thing said", "the last thing said")
    w = assemble(replay(*events), max_turns=3, budget_tokens=2_000, item_chars=200)
    assert w.text.index("the first thing said") < w.text.index("the last thing said")


def test_a_window_with_room_for_everything_is_unchanged_by_the_reorder():
    """The common small-transcript case must not move at all."""
    events = [*turn("ask one", "answer one"), *turn("ask two", "answer two")]
    w = assemble(replay(*events))
    assert w.text.splitlines() == [
        "user: ask one", "assistant: answer one",
        "user: ask two", "assistant: answer two",
    ]


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


# ---------- tools=False: the aegis_read_peer default (#24) ------------------

def test_collapsed_tool_calls_become_one_line_per_run():
    w = assemble(replay(
        UserMessage(text="fix the test"),
        ToolUse(name="Bash", summary="make test"),
        ToolResult(text="1 failed", is_error=False),
        ToolUse(name="Read", summary="src/a.py"),
        ToolResult(text="def a(): ...", is_error=False),
        ToolUse(name="Bash", summary="make test"),
        ToolResult(text="all green", is_error=False),
        AssistantText(text="fixed"),
        Result(duration_ms=1, is_error=False),
    ), tools=False)
    lines = w.text.splitlines()
    assert lines == ["user: fix the test", "tools: Bash×2, Read", "assistant: fixed"]


def test_prose_between_tool_calls_splits_the_runs():
    w = assemble(replay(
        ToolUse(name="Read", summary="a.py"),
        AssistantText(text="now the other file"),
        ToolUse(name="Edit", summary="b.py"),
        Result(duration_ms=1, is_error=False),
    ), tools=False)
    assert w.text.splitlines() == [
        "tools: Read", "assistant: now the other file", "tools: Edit"]


def test_collapsing_happens_before_the_budget_is_spent():
    """Collapsing after admission would only shorten a window the tool calls
    had already filled. Collapsed first, the room they no longer take goes
    to the conversation."""
    events = [UserMessage(text="the question that opened the turn " * 20)]
    for i in range(40):
        events += [ToolUse(name="Read", summary=f"file{i}.py " * 20),
                   ToolResult(text="x" * 400, is_error=False)]
    events += [AssistantText(text="done"), Result(duration_ms=1, is_error=False)]
    budget = 400
    assert "file0.py" not in assemble(replay(*events), budget_tokens=budget).text
    w = assemble(replay(*events), budget_tokens=budget, tools=False)
    assert "tools: Read×40" in w.text
    assert "the question that opened the turn" in w.text
