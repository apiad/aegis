"""The mid-turn recap: what this session is doing INSIDE a turn that has
not closed. The existing recap fires when a turn ends, which is exactly
the moment the answer stops being useful to a dashboard."""
import asyncio

import pytest

from aegis.config import Agent
from aegis.drivers.oneshot import Generation
from aegis.digest.models import TurnFacts
from aegis.recap import FleetRecap, recap_in_flight


@pytest.fixture
def fake_agent():
    return Agent(harness="claude-code", model="opus")


class FakeDriver:
    supports_oneshot = True

    def __init__(self, value=None, fail=False):
        self.value = value
        self.fail = fail
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((schema, instructions))
        if self.fail:
            return Generation()
        return Generation(value=self.value, model="haiku", duration_ms=4700,
                          cost_usd=0.00363)


def test_a_good_call_returns_both_lines(fake_agent):
    d = FakeDriver(FleetRecap(done="landed 3 tests", doing="closing the loop"))
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.ok is True
    assert r.done == "landed 3 tests"
    assert r.doing == "closing the loop"


def test_a_failed_call_is_a_missing_answer_not_an_exception(fake_agent):
    """Best-effort by contract, like titlegen: a dashboard must not break
    because a $0.0036 call did not come back."""
    d = FakeDriver(fail=True)
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.ok is False
    assert r.done == "" and r.doing == ""


def test_the_cost_rides_back_with_the_answer(fake_agent):
    """The band shows the running recap spend; it can only do that if each
    call reports what it cost."""
    d = FakeDriver(FleetRecap(done="a", doing="b"))
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.cost_usd == 0.00363


def test_the_window_is_the_generous_one(fake_agent):
    """Measured 2026-09-16: a full window costs ~546 tokens over the 1,027
    floor, $0.0012, and produces lines that name files and counts. Squeezing
    it saves nothing and measurably degrades the answer."""
    from aegis.recap import IN_FLIGHT_WINDOW

    assert IN_FLIGHT_WINDOW["budget_tokens"] >= 2_000
