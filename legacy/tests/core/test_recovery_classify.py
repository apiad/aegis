"""The retry budget is the control, not a taxonomy of failure reasons.

We have four drivers and two protocols and no stable cross-harness
vocabulary for why a turn ended badly. A hand-written list of transient
reasons is wrong the day a provider changes a string, and wrong silently:
it goes terminal where it should have retried, which looks exactly like
the bug this plane exists to fix. So everything that is not a clean end
is transient until the budget runs out.
"""
from __future__ import annotations

import inspect

import pytest

from aegis.core.recovery import Outcome, classify
from aegis.tui.state import AgentState


def test_ready_is_done():
    assert classify(AgentState.ready, attempts=0, max_attempts=2) is Outcome.done


def test_ready_is_done_even_on_the_last_attempt():
    """A clean end is a clean end; the budget never turns an answer into a
    failure."""
    assert classify(AgentState.ready, attempts=9, max_attempts=2) is Outcome.done


def test_error_with_budget_left_is_transient():
    assert classify(AgentState.error, attempts=0, max_attempts=2) is Outcome.transient


def test_error_with_budget_exhausted_is_terminal():
    assert classify(AgentState.error, attempts=2, max_attempts=2) is Outcome.terminal


def test_max_attempts_of_one_is_terminal_on_the_first_bad_end():
    """The park-immediately model, for anyone who wants it."""
    assert classify(AgentState.error, attempts=1, max_attempts=1) is Outcome.terminal


def test_the_budget_is_the_only_source_of_terminal():
    """`classify` used to take `cancelled=` and `over_budget=`, and two rows
    here pinned them — an API contract with no reachable caller, on the one
    function the workflow and group callers will read before wiring
    themselves to it.

    Cancelling never reaches `classify`: `QueueManager.cancel` pops
    `_workers` before closing the worker, so the finalize the close triggers
    early-returns (`tests/test_queue_manager.py` covers that path). Budgets
    are evaluated at enqueue, so there is no mid-flight over-budget path.
    Read off the signature so a knob cannot come back dead.
    """
    params = set(inspect.signature(classify).parameters) - {"state"}
    assert params == {"attempts", "max_attempts"}, (
        f"classify grew {sorted(params - {'attempts', 'max_attempts'})}. "
        "If it has a caller, say so here; if it does not, it is a contract "
        "the next caller will wire itself to for nothing."
    )


@pytest.mark.parametrize("state", [AgentState.working, AgentState.error])
def test_every_non_ready_state_is_transient_while_budget_remains(state):
    """The default arm. Nothing enumerates stop_reason."""
    assert classify(state, attempts=0, max_attempts=2) is Outcome.transient
