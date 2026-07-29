"""Who may close whom, and when.

Closing an agent throws away a live session, so the gate is deliberately
strict: only the agent that spawned it, and only once the target is
demonstrably finished — no live turn, and nothing in flight that would
be silently dropped with it.
"""
from __future__ import annotations

from aegis.core.close_guard import CloseFacts, refuse_reasons


def _idle(**over) -> CloseFacts:
    facts = dict(exists=True, spawned_by="parent", state="ready",
                 monitors=0, reminders=0, inbox_depth=0,
                 worker_label=None, loop_armed=False, claims=0)
    facts.update(over)
    return CloseFacts(**facts)


def test_a_spawned_idle_agent_may_be_closed():
    assert refuse_reasons(_idle(), requester="parent", target="child") == []


def test_unknown_target():
    reasons = refuse_reasons(_idle(exists=False), requester="parent",
                             target="ghost")
    assert any("no session" in r for r in reasons)


def test_only_the_spawner_may_close():
    """Provenance is the whole point: an agent may reap what it started,
    not tabs the operator opened or a peer's workers."""
    assert refuse_reasons(_idle(spawned_by="someone-else"),
                          requester="parent", target="child")
    assert refuse_reasons(_idle(spawned_by=None),
                          requester="parent", target="child")
    reasons = refuse_reasons(_idle(spawned_by=None), requester="parent",
                             target="child")
    assert any("did not spawn" in r for r in reasons)


def test_a_working_agent_is_never_closed():
    reasons = refuse_reasons(_idle(state="working"), requester="parent",
                             target="child")
    assert any("mid-turn" in r for r in reasons)


def test_work_in_flight_blocks_and_every_reason_is_reported():
    """All the reasons at once — one round trip should tell the caller
    everything it has to wait for, not the first thing that tripped."""
    reasons = refuse_reasons(
        _idle(monitors=2, reminders=1, inbox_depth=3,
              worker_label="impl#7f3a", loop_armed=True, claims=4),
        requester="parent", target="child")
    joined = " | ".join(reasons)
    assert "2 live monitor" in joined
    assert "1 pending reminder" in joined
    assert "3 undelivered" in joined
    assert "impl#7f3a" in joined
    assert "loop" in joined
    assert "4 file claim" in joined


def test_self_close_is_refused():
    reasons = refuse_reasons(_idle(spawned_by="parent"), requester="parent",
                             target="parent")
    assert any("itself" in r for r in reasons)
