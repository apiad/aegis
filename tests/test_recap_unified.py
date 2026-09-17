"""One recap schema and one prompt; only the window changes."""

import pytest

from aegis.digest.models import TurnFacts
from aegis.recap import (
    IN_FLIGHT_WINDOW,
    SESSION_WINDOW,
    SYSTEM,
    TURN_WINDOW,
    Recap,
    StandingRecap,
    recap_in_flight,
    recap_session,
    recap_turn,
)

V = StandingRecap(task="Ship the Help view", outcome="Fixed the last rendering bugs.",
                  next="Final build.", attention="waiting")


class _Gen:
    def __init__(self, value):
        self.value, self.model, self.duration_ms, self.cost_usd = value, "haiku", 1, 0.01


class _Driver:
    def __init__(self):
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, system, *rest):
        self.calls.append((schema, system, rest))
        return _Gen(V)


@pytest.mark.parametrize("fn", [recap_turn, recap_session, recap_in_flight])
async def test_every_caller_uses_the_one_schema_and_prompt(fn):
    d = _Driver()
    got = await fn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    schema, system, _rest = d.calls[0]
    assert schema is StandingRecap
    assert system.startswith(SYSTEM)
    assert (got.task, got.line, got.next, got.attention) == (
        "Ship the Help view", "Fixed the last rendering bugs.", "Final build.", "waiting")


async def test_the_mid_turn_call_asks_for_the_present():
    d = _Driver()
    await recap_in_flight(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert "still running" in d.calls[0][1]


async def test_the_previous_task_is_passed_as_a_hint():
    d = _Driver()
    await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp",
                     previous_task="Ship the Help view")
    assert any("Previous task: Ship the Help view" in part for part in d.calls[0][2])


async def test_no_previous_task_sends_no_hint():
    d = _Driver()
    await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert not any("Previous task" in part for part in d.calls[0][2])


def test_the_prompt_forbids_the_inventory():
    for word in ("files", "commits", "hashes", "task ids", "test counts", "tool calls"):
        assert word in SYSTEM
    assert "Name files and counts" not in SYSTEM


def test_the_windows():
    assert TURN_WINDOW == dict(max_turns=3, budget_tokens=3_000, item_chars=300)
    assert IN_FLIGHT_WINDOW == dict(max_turns=2, budget_tokens=2_500, item_chars=240)
    assert SESSION_WINDOW == dict(max_turns=8, budget_tokens=8_000, item_chars=300)


def test_text_is_the_outcome_and_block_lists_the_three_fields():
    r = Recap(task="T", line="O", next="", ok=True)
    assert r.text == "O"
    assert r.block == "- **task:** T\n- **outcome:** O"
