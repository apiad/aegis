"""The judge replaces the coda as the thing that ends a loop."""
import asyncio

import pytest

from aegis.core.loop import LoopState
from aegis.core.loop_judge import Judgement
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.tui.state import AgentState

from tests.digest_harness import build_session

MOVED = TurnFacts(repos=(RepoDelta(name="a", commits=(CommitLine("a1",
                                                                 "x"),)),))
STILL = TurnFacts(assistant_tail="thinking")


async def _settle(session):
    """Let every chained turn run to completion."""
    for _ in range(200):
        await asyncio.sleep(0)
        if session.state is not AgentState.working:
            return


def test_render_is_the_instruction_verbatim():
    """The coda is gone. Verbatim matters: the previous turn may have
    ended somewhere unhelpful."""
    s = LoopState(text="keep going")
    assert s.render() == "keep going"
    assert "aegis_loop_stop" not in s.render()


def test_render_appends_the_addendum_without_replacing_the_goal():
    s = LoopState(text="keep going")
    out = s.render(addendum="the UI is still missing")
    assert out.startswith("keep going")
    assert "the UI is still missing" in out


def test_the_still_streak_counts_consecutive_motionless_turns():
    s = LoopState(text="x")
    assert s.still_streak == 0
    s.note(STILL)
    s.note(STILL)
    assert s.still_streak == 2
    s.note(MOVED)
    assert s.still_streak == 0


def test_note_tolerates_missing_facts():
    """last_facts is None before the first turn completes."""
    s = LoopState(text="x")
    s.note(None)
    assert s.still_streak == 1


@pytest.mark.asyncio
async def test_a_done_verdict_stops_the_loop(tmp_path, monkeypatch):
    async def verdict(**_kw):
        return Judgement(verdict="done", reason="all wired", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 20)
    await _settle(s)
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_a_done_verdict_does_not_consume_an_iteration(tmp_path,
                                                            monkeypatch):
    seen = {}

    async def verdict(**kw):
        seen["iteration"] = kw["iteration"]
        return Judgement(verdict="done", reason="done", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 20)
    await _settle(s)
    assert seen["iteration"] == 1


@pytest.mark.asyncio
async def test_a_failed_judge_continues(tmp_path, monkeypatch):
    """A failed API call must never silently end a night of work."""
    async def verdict(**_kw):
        return Judgement(verdict="continue", ok=False, error="boom")

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, harness = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 3)
    await _settle(s)
    # Ran to the cap rather than stopping on the failure.
    assert len(harness.sent) == 3


@pytest.mark.asyncio
async def test_the_first_delivery_is_not_judged(tmp_path, monkeypatch):
    """arm_loop chains immediately when idle; there is no turn to judge."""
    calls = []

    async def verdict(**kw):
        calls.append(kw)
        return Judgement(verdict="done", reason="stop now", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, harness = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 20)
    await _settle(s)
    # The instruction went out once before any judging happened.
    assert harness.sent
    assert "keep going" in harness.sent[0]
    assert calls and calls[0]["iteration"] == 1


@pytest.mark.asyncio
async def test_an_exhausted_loop_is_not_judged(tmp_path, monkeypatch):
    """No point paying for a verdict on a loop that is stopping anyway."""
    calls = []

    async def verdict(**kw):
        calls.append(kw)
        return Judgement(verdict="continue", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 1)
    await _settle(s)
    assert calls == []
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_the_addendum_reaches_the_agent(tmp_path, monkeypatch):
    async def verdict(**_kw):
        return Judgement(verdict="continue", reason="more to do",
                         addendum="THE-UI-IS-MISSING", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, harness = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 2)
    await _settle(s)
    assert any("THE-UI-IS-MISSING" in m for m in harness.sent)
    assert all("keep going" in m for m in harness.sent)


@pytest.mark.asyncio
async def test_loop_stop_is_advisory_and_does_not_reap(tmp_path):
    """The inversion: the tool records a claim, the judge decides."""
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 20)
    s.stop_loop("I think I'm done", advisory=True)
    assert s.loop_status() is not None
    assert s._loop.advisory == "I think I'm done"


@pytest.mark.asyncio
async def test_the_advisory_reaches_the_judge_then_is_consumed(
        tmp_path, monkeypatch):
    seen = []

    async def verdict(**kw):
        seen.append(kw["advisory"])
        return Judgement(verdict="continue", reason="not yet", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 3)
    s.stop_loop("I think I'm done", advisory=True)
    await _settle(s)
    assert seen[0] == "I think I'm done"
    # Consumed: the judge has already rejected it once, and re-presenting
    # a stale claim every iteration would bias every later verdict.
    assert seen[1:] == [""] * len(seen[1:])


@pytest.mark.asyncio
async def test_the_operator_can_still_stop_a_loop_outright(tmp_path):
    s, _ = build_session(tmp_path, agents={})
    s.arm_loop("keep going", 20)
    s.stop_loop("operator stopped it")
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_the_operator_stop_and_the_agent_stop_differ(tmp_path):
    """Only the AGENT's stop is advisory.

    `/loop stop` is the operator saying stop, and the operator is not
    second-guessed by a judge. Collapsing the two would either make
    aegis_loop_stop authoritative again (restoring the 2026-07-30 burn) or
    make the operator unable to stop their own loop.
    """
    from aegis.queue.loop import LoopService

    class FakeSM:
        def __init__(self, s):
            self._s = s

        def get(self, handle):
            return self._s

    s, _ = build_session(tmp_path, agents={})
    svc = LoopService(FakeSM(s))

    svc.arm(from_handle="p", text="keep going", max_iterations=9)
    agent_result = svc.stop(from_handle="p", reason="I think I'm done",
                            advisory=True)
    assert agent_result["noted"] is True
    assert s.loop_status() is not None          # the agent did NOT end it

    operator_result = svc.stop(from_handle="p", reason="enough")
    assert operator_result["stopped"] is True
    assert s.loop_status() is None              # the operator did
