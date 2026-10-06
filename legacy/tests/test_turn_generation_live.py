"""One real round-trip per generator. Behind the `live` marker.

Run with: uv run python -m pytest tests/test_turn_generation_live.py -v
(the fast suite uses -m "not live"; never -k, which eats substrings)

These hit the real `claude` CLI. A failure here is a real failure, not a
skip — the schemas and prompts are only load-bearing against a real model.
"""
import shutil

import pytest

from aegis.config import Agent
from aegis.core.loop_judge import judge
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers import get_driver
from aegis.events import AssistantText
from aegis.recap import recap_session, recap_turn

pytestmark = pytest.mark.live

FACTS = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("51430de",
                                                       "docs: the spec"),)),))


class Replay:
    events = [AssistantText(text="I wrote the spec and committed it.")]


@pytest.fixture
def driver_and_agent():
    if not shutil.which("claude"):
        pytest.skip("claude not on PATH")
    return get_driver("claude-code"), Agent(harness="claude-code",
                                            model="haiku")


@pytest.mark.asyncio
async def test_a_real_turn_recap_comes_back_as_one_line(driver_and_agent):
    driver, agent = driver_and_agent
    got = await recap_turn(replay=Replay(), facts=FACTS, driver=driver,
                           agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.line.strip()
    assert "\n" not in got.line.strip()


@pytest.mark.asyncio
async def test_a_real_session_recap_fills_all_three_fields(
        driver_and_agent):
    driver, agent = driver_and_agent
    got = await recap_session(replay=Replay(), facts=FACTS, driver=driver,
                              agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.building.strip() and got.done.strip()


@pytest.mark.asyncio
async def test_a_real_judge_returns_a_known_verdict(driver_and_agent):
    driver, agent = driver_and_agent
    got = await judge(instruction="write and commit the spec", iteration=2,
                      max_iterations=20, facts=FACTS, still_streak=0,
                      advisory="", replay=Replay(), driver=driver,
                      agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.verdict in ("continue", "done", "stuck")


@pytest.mark.asyncio
async def test_a_real_judge_rejects_a_premature_stop(driver_and_agent):
    """The 2026-07-30 burn, as a test.

    The agent claims done at iteration 1 of 20 having built only the
    backend. A judge reading the instruction at its widest should not
    accept that — the operator cannot yet use anything.
    """
    driver, agent = driver_and_agent
    facts = TurnFacts(repos=(RepoDelta(
        name="warden", files_written=3,
        commits=(CommitLine("aa11", "feat: wire the two ML rails"),)),))
    got = await judge(
        instruction="wire it up all on warden — I want to load and unload "
                    "models from the UI",
        iteration=1, max_iterations=20, facts=facts, still_streak=0,
        advisory="I wired both ML rails to the inference engine and "
                 "verified them live. The instruction is satisfied.",
        replay=Replay(), driver=driver, agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.verdict == "continue", got.reason
