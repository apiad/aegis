"""Tests for the canonical ContextUpdate event + CostUsage dataclass.

ACP emits these mid-turn (UsageUpdate / CurrentModeUpdate /
SessionInfoUpdate). Claude doesn't have an equivalent — claude reports
all telemetry at turn end on the Result event. ContextUpdate is the
ACP-only path; downstream consumers (status bar, metrics) can
subscribe without driver branching."""
from __future__ import annotations

import pytest

from aegis.events import ContextUpdate, CostUsage


def test_cost_usage_basic():
    c = CostUsage(amount_usd=0.0123,
                  context_used=12345, context_size=200000)
    assert c.amount_usd == 0.0123
    assert c.context_used == 12345
    assert c.context_size == 200000


def test_cost_usage_partial():
    """All fields are optional — different ACP sources populate
    different subsets."""
    c = CostUsage(amount_usd=None, context_used=100, context_size=None)
    assert c.amount_usd is None
    assert c.context_used == 100


def test_context_update_carries_cost():
    u = ContextUpdate(cost=CostUsage(
        amount_usd=0.01, context_used=100, context_size=200000))
    assert u.cost is not None
    assert u.cost.amount_usd == 0.01


def test_context_update_carries_mode():
    u = ContextUpdate(mode="plan")
    assert u.mode == "plan"
    assert u.cost is None
    assert u.title is None


def test_context_update_carries_title():
    u = ContextUpdate(title="reading the spec")
    assert u.title == "reading the spec"


def test_context_update_all_optional():
    u = ContextUpdate()
    assert u.cost is None
    assert u.mode is None
    assert u.title is None


def test_context_update_is_event_type():
    from aegis.events import Event
    import typing
    members = typing.get_args(Event)
    assert ContextUpdate in members
