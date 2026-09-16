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

    def __init__(self, value=None, fail=False, raises=None):
        self.value = value
        self.fail = fail
        self.raises = raises
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((agent, schema, instructions))
        if self.raises is not None:
            raise self.raises
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


def test_the_driver_is_handed_the_in_flight_schema_prompt_and_facts(fake_agent):
    """What is paid for is what reaches the driver: the `{done, doing}`
    schema, the in-flight system prompt rather than the turn recap's, and
    the FACTS block the prompt tells the model to prefer."""
    from aegis.digest.models import CommitLine, RepoDelta
    from aegis.recap import _IN_FLIGHT_SYSTEM, _TURN_SYSTEM

    facts = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                       commits=(CommitLine("a1", "feat: x"),)),))
    d = FakeDriver(FleetRecap(done="a", doing="b"))
    asyncio.run(recap_in_flight(replay=[], facts=facts, driver=d,
                                agent=fake_agent, cwd="."))
    ((agent, schema, instructions),) = d.calls
    assert agent is fake_agent
    assert schema is FleetRecap
    assert instructions[0] == _IN_FLIGHT_SYSTEM
    assert _TURN_SYSTEM not in instructions
    assert any("feat: x" in i for i in instructions[1:])


def test_a_driver_that_raises_is_a_missing_answer(fake_agent):
    d = FakeDriver(raises=RuntimeError("claude -p exited 1"))
    r = asyncio.run(recap_in_flight(replay=[], facts=TurnFacts(), driver=d,
                                    agent=fake_agent, cwd="."))
    assert r.ok is False
    assert "claude -p exited 1" in r.error
    assert len(d.calls) == 1


async def test_recap_in_flight_for_bills_the_text_generation_agent(
        tmp_path, monkeypatch):
    """Through the real `_resolve`: the config at `root` names the billing
    profile, and the driver is handed that profile rather than the
    session's own model."""
    from aegis.recap import _IN_FLIGHT_SYSTEM, recap_in_flight_for

    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "  haiku:\n    provider: claude-code\n"
        "    model: claude-haiku-4-5-20251001\n"
        "default_agent: opus\ntext_generation: haiku\n")
    opus = Agent(harness="claude-code", model="opus")
    haiku = Agent(harness="claude-code", model="claude-haiku-4-5-20251001")
    d = FakeDriver(FleetRecap(done="a", doing="b"))
    harnesses = []
    monkeypatch.setattr("aegis.drivers.get_driver",
                        lambda harness: harnesses.append(harness) or d)
    recap = await recap_in_flight_for(
        state_dir=tmp_path, log_id="x", facts=TurnFacts(), agent=opus,
        agents={"opus": opus, "haiku": haiku}, cwd=str(tmp_path), root=tmp_path)
    assert recap.ok, recap.error
    assert harnesses == ["claude-code"]
    ((agent, schema, instructions),) = d.calls
    assert agent is haiku
    assert schema is FleetRecap
    assert instructions[0] == _IN_FLIGHT_SYSTEM


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


def test_a_replay_that_cannot_be_assembled_is_a_missing_answer(fake_agent):
    """Best-effort by contract: the in-flight caller hands over a live event
    list, and a malformed one must come back as ok=False, never raise."""
    d = FakeDriver(FleetRecap(done="a", doing="b"))
    r = asyncio.run(recap_in_flight(replay=object(), facts=TurnFacts(),
                                    driver=d, agent=fake_agent, cwd="."))
    assert r.ok is False
    assert r.error
    assert d.calls == [], "the driver must not be paid for a window that failed"


from aegis.config import FleetConfig
from aegis.recap.gate import should_fleet_recap

ON = FleetConfig(recap="watched", recap_after_s=60, recap_interval_s=120)


def test_an_idle_session_is_never_worth_a_call():
    """Its last turn recap already exists and already says what landed."""
    assert should_fleet_recap(state="ready", turn_s=0, since_last_s=999,
                              watchers=1, cfg=ON) is False


def test_a_young_turn_waits():
    """Under a minute you would read the line before it refreshed."""
    assert should_fleet_recap(state="working", turn_s=30, since_last_s=999,
                              watchers=1, cfg=ON) is False


def test_a_working_watched_turn_past_the_threshold_fires():
    assert should_fleet_recap(state="working", turn_s=61, since_last_s=999,
                              watchers=1, cfg=ON) is True


def test_nobody_watching_means_nobody_pays():
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=999,
                              watchers=0, cfg=ON) is False


def test_the_interval_holds_between_calls():
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=30,
                              watchers=1, cfg=ON) is False
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=121,
                              watchers=1, cfg=ON) is True


def test_no_mode_pays_for_a_session_nobody_watches():
    """There is no always-on mode. A config object that somehow carries
    another string still needs a watcher: the gate has no branch that
    skips the watcher count."""
    cfg = FleetConfig(recap="on", recap_after_s=60, recap_interval_s=120)
    assert should_fleet_recap(state="working", turn_s=61, since_last_s=999,
                              watchers=0, cfg=cfg) is False


def test_off_never_fires():
    cfg = FleetConfig(recap="off")
    assert should_fleet_recap(state="working", turn_s=999, since_last_s=999,
                              watchers=9, cfg=cfg) is False


async def test_in_flight_recap_without_text_generation_never_calls_the_driver(
        tmp_path, monkeypatch):
    from aegis.config import Agent
    from aegis.recap import recap_in_flight_for
    from aegis.digest.models import TurnFacts

    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    opus = Agent(harness="claude-code", model="opus")
    driven = []
    monkeypatch.setattr("aegis.drivers.get_driver",
                        lambda harness: driven.append(harness))
    recap = await recap_in_flight_for(
        state_dir=tmp_path, log_id="x", facts=TurnFacts(), agent=opus,
        agents={"opus": opus}, cwd=str(tmp_path), root=tmp_path)
    assert driven == []
    assert not recap.ok
    assert recap.error == (
        "text_generation: must name a configured agent profile to bill "
        "the mid-turn recap (it is unset, or names no profile)")
