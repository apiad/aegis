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
