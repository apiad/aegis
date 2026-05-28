"""Tests for the canonical AgentPlan event + PlanEntry dataclass.

AgentPlan unifies claude's TodoWrite tool input and ACP's
AgentPlanUpdate notification. Driver wiring + render branch land in
follow-on commits; this file pins the dataclass shape and the codec
roundtrip.
"""
from __future__ import annotations

import pytest

from aegis.events import AgentPlan, PlanEntry


def test_plan_entry_basic():
    e = PlanEntry(content="write the test", status="pending")
    assert e.content == "write the test"
    assert e.status == "pending"
    assert e.priority == "medium"


def test_plan_entry_with_priority():
    e = PlanEntry(content="ship it", status="in_progress", priority="high")
    assert e.priority == "high"


def test_plan_entry_is_hashable():
    """Frozen dataclasses go into tuples on AgentPlan; ensure equality
    semantics hold."""
    a = PlanEntry(content="x", status="pending")
    b = PlanEntry(content="x", status="pending")
    assert a == b
    assert hash(a) == hash(b)


def test_agent_plan_basic():
    p = AgentPlan(entries=(
        PlanEntry(content="A", status="completed"),
        PlanEntry(content="B", status="in_progress"),
        PlanEntry(content="C", status="pending"),
    ))
    assert len(p.entries) == 3
    assert p.entries[1].status == "in_progress"


def test_agent_plan_empty():
    p = AgentPlan(entries=())
    assert p.entries == ()


def test_agent_plan_is_event_type():
    """AgentPlan must be part of the canonical Event union so isinstance
    checks in the renderer / observers pick it up."""
    from aegis.events import Event
    import typing
    # Event is a typing.Union; getargs returns the tuple of constituent types.
    members = typing.get_args(Event)
    assert AgentPlan in members
