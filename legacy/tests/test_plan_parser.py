"""The parser side of plan tracking.

Two sources speak in snapshots (claude's legacy TodoWrite, ACP's
AgentPlanUpdate) and one speaks in deltas with ids (claude's current
TaskCreate / TaskUpdate family). The parser folds all three into the
canonical cumulative AgentPlan event so everything downstream — render,
codec, web, HTML export — sees one shape.
"""
import json

from aegis.events import (
    AgentPlan, ContextUpdate, ParserState, PlanEntry, ToolUse, parse,
)
from aegis.state.event_codec import decode_event, encode_event


# -- Task 1: PlanEntry carries id + active_form ----------------------

def test_plan_entry_carries_id_and_active_form():
    e = PlanEntry(content="Write the spec", status="in_progress",
                  id="7", active_form="Writing the spec")
    assert e.id == "7"
    assert e.active_form == "Writing the spec"


def test_plan_entry_defaults_are_none_for_snapshot_sources():
    e = PlanEntry(content="x", status="pending")
    assert e.id is None and e.active_form is None


def test_codec_round_trips_new_plan_entry_fields():
    ev = AgentPlan(entries=(
        PlanEntry(content="a", status="completed", id="1",
                  active_form="Doing a"),
        PlanEntry(content="b", status="pending"),
    ))
    assert decode_event(encode_event(ev)) == ev


def test_codec_round_trips_parent_tool_use_id():
    """Without this a replayed subagent plan is indistinguishable from a
    top-level one, and the subagent's short list overwrites its parent's."""
    ev = AgentPlan(entries=(PlanEntry(content="a", status="pending"),),
                   parent_tool_use_id="toolu_123")
    assert decode_event(encode_event(ev)) == ev


# -- Task 2: the Task* family folds into cumulative AgentPlans -------

def _use(name, tool_input, tid):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}})


def _result(text, tid):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}})


def test_task_create_becomes_a_cumulative_plan():
    st = ParserState()
    ev = parse(_use("TaskCreate", {"subject": "Explore", "description": "d",
                                   "activeForm": "Exploring"}, "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.content for e in ev.entries] == ["Explore"]
    assert ev.entries[0].status == "pending"
    assert ev.entries[0].active_form == "Exploring"

    ev = parse(_use("TaskCreate", {"subject": "Write", "description": "d"},
                    "t2"), st)
    assert [e.content for e in ev.entries] == ["Explore", "Write"]


def test_task_create_id_is_backfilled_from_the_result():
    """TaskCreate's tool_use carries no id at all — it arrives in the
    result text as "Task #7 created successfully: ...". Without the
    backfill, no later TaskUpdate can ever resolve its target."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "Explore", "description": "d"}, "t1"),
          st)
    parse(_result("Task #7 created successfully: Explore", "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "7",
                                   "status": "in_progress"}, "t2"), st)
    assert isinstance(ev, AgentPlan)
    assert ev.entries[0].status == "in_progress"
    assert ev.entries[0].id == "7"


def test_task_confirmation_result_is_swallowed_not_orphaned():
    """The parser returns AgentPlan instead of a ToolUse, so this result
    has nothing to fold into and would mount as a standalone block
    (pane.py:1878) — one noise row per task, which is the whole thing we
    are removing. ContextUpdate renders as None."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "Explore", "description": "d"}, "t1"),
          st)
    ev = parse(_result("Task #7 created successfully: Explore", "t1"), st)
    assert isinstance(ev, ContextUpdate)


def test_task_update_status_and_relabel():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    parse(_result("Task #1 created successfully: A", "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "1", "status": "in_progress",
                                   "activeForm": "Doing A"}, "t2"), st)
    assert ev.entries[0].status == "in_progress"
    assert ev.entries[0].active_form == "Doing A"


def test_task_update_can_delete():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    parse(_result("Task #1 created successfully: A", "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "1", "status": "deleted"}, "t2"),
               st)
    assert ev.entries == ()


def test_task_get_is_a_read_and_stays_a_tool_call():
    """TaskGet returns one task, not the list, so it is not a plan
    channel and renders as an ordinary tool call."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    assert isinstance(parse(_use("TaskGet", {"taskId": "1"}, "t3"), st),
                      ToolUse)


def test_a_non_plan_tool_result_is_never_swallowed():
    """The swallow must not be greedy: only the plan channel's own
    confirmations disappear."""
    st = ParserState()
    parse(_use("TaskGet", {"taskId": "1"}, "t1"), st)
    ev = parse(_result("#1 [pending] A", "t1"), st)
    assert not isinstance(ev, ContextUpdate)
    assert not isinstance(ev, AgentPlan)


def test_ordinary_tool_results_are_untouched():
    st = ParserState()
    parse(_use("Bash", {"command": "ls"}, "t1"), st)
    ev = parse(_result("a.txt", "t1"), st)
    assert ev.text == "a.txt"


def test_todowrite_snapshot_path_is_unchanged():
    st = ParserState()
    ev = parse(_use("TodoWrite", {"todos": [
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "pending"}]}, "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.status for e in ev.entries] == ["completed", "pending"]


def test_task_update_for_an_unknown_id_is_ignored_not_fatal():
    """A TaskUpdate can arrive for a task the parser never saw created —
    a resumed session, a truncated log. It must not raise."""
    st = ParserState()
    ev = parse(_use("TaskUpdate", {"taskId": "99", "status": "completed"},
                    "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert ev.entries == ()


def test_subagent_task_plans_carry_their_parent_id():
    st = ParserState()
    line = json.dumps({
        "type": "assistant",
        "parent_tool_use_id": "toolu_parent",
        "message": {"content": [{
            "type": "tool_use", "id": "t1", "name": "TaskCreate",
            "input": {"subject": "sub", "description": "d"}}]}})
    ev = parse(line, st)
    assert isinstance(ev, AgentPlan)
    assert ev.parent_tool_use_id == "toolu_parent"



# -- rehydration: surviving a restart --------------------------------

_LISTING = (
    "#1 [completed] Explore aegis plan-rendering context\n"
    "#2 [completed] Clarify design questions with Alex\n"
    "#11 [in_progress] T8: AgentPlan replaces in place\n"
    "#12 [pending] T9: PlanDock, F3 and /tasks"
)


def test_task_list_result_rehydrates_a_plan_the_parser_never_saw_built():
    """ParserState resets on restart, so every task created before it is
    invisible and a TaskUpdate against those ids is silently dropped.
    TaskList returns the whole list — use it to recover."""
    st = ParserState()
    parse(_use("TaskList", {}, "t1"), st)
    ev = parse(_result(_LISTING, "t1"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.id for e in ev.entries] == ["1", "2", "11", "12"]
    assert [e.status for e in ev.entries] == [
        "completed", "completed", "in_progress", "pending"]
    assert ev.entries[2].content == "T8: AgentPlan replaces in place"


def test_a_rehydrated_plan_accepts_updates_for_its_ids():
    """The point of rehydrating: updates land again."""
    st = ParserState()
    parse(_use("TaskList", {}, "t1"), st)
    parse(_result(_LISTING, "t1"), st)
    ev = parse(_use("TaskUpdate", {"taskId": "11",
                                   "status": "completed"}, "t2"), st)
    assert [e.status for e in ev.entries] == [
        "completed", "completed", "completed", "pending"]


def test_rehydration_keeps_active_form_the_listing_does_not_carry():
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "T8: AgentPlan replaces in place",
                              "description": "d",
                              "activeForm": "Folding revisions"}, "t1"), st)
    parse(_result("Task #11 created successfully: x", "t1"), st)
    parse(_use("TaskList", {}, "t2"), st)
    ev = parse(_result("#11 [in_progress] T8: AgentPlan replaces in place",
                       "t2"), st)
    assert ev.entries[0].active_form == "Folding revisions"


def test_task_list_use_shows_the_current_plan_rather_than_a_tool_row():
    """A TaskList call is the agent asking what its plan is; rendering the
    plan is the honest answer, and it keeps the transcript free of a row
    that says nothing."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    ev = parse(_use("TaskList", {}, "t2"), st)
    assert isinstance(ev, AgentPlan)
    assert [e.content for e in ev.entries] == ["A"]


def test_a_non_listing_result_does_not_wipe_the_plan():
    """An empty or unparseable TaskList result must leave state alone
    rather than silently clearing every task."""
    st = ParserState()
    parse(_use("TaskCreate", {"subject": "A", "description": "d"}, "t1"), st)
    parse(_result("Task #1 created successfully: A", "t1"), st)
    parse(_use("TaskList", {}, "t2"), st)
    ev = parse(_result("(no tasks)", "t2"), st)
    assert [e.content for e in ev.entries] == ["A"]


def test_task_get_stays_an_ordinary_tool_call():
    """TaskGet returns one task, not the list — it is not a plan channel."""
    st = ParserState()
    assert isinstance(parse(_use("TaskGet", {"taskId": "1"}, "t1"), st),
                      ToolUse)
