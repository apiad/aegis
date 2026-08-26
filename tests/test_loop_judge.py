"""The judge. Its failure mode must be CONTINUE — a failed API call must
never silently end a night of work."""
import pytest

from aegis.core.loop_judge import LoopVerdict, judge
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation


class FakeDriver:
    supports_oneshot = True

    def __init__(self, value=None, raises=False):
        self.value, self.raises = value, raises
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((schema, instructions))
        if self.raises:
            raise RuntimeError("driver exploded")
        return Generation(value=self.value, model="haiku",
                          duration_ms=900, cost_usd=0.02)


class FakeReplay:
    events = []


MOVED = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("a1", "feat: x"),)),))


async def _judge(driver, **over):
    kw = dict(instruction="wire the recap end to end", iteration=3,
              max_iterations=20, facts=MOVED, still_streak=0, advisory="",
              replay=FakeReplay(), driver=driver, agent=object(), cwd=".")
    kw.update(over)
    return await judge(**kw)


@pytest.mark.asyncio
async def test_a_continue_verdict_carries_its_addendum():
    d = FakeDriver(LoopVerdict(verdict="continue", reason="UI unbuilt",
                               addendum="the model tab is still missing"))
    got = await _judge(d)
    assert got.ok and got.verdict == "continue"
    assert got.addendum == "the model tab is still missing"


@pytest.mark.asyncio
async def test_a_done_verdict_is_returned_as_done():
    d = FakeDriver(LoopVerdict(verdict="done", reason="all wired"))
    assert (await _judge(d)).verdict == "done"


@pytest.mark.asyncio
async def test_a_done_verdict_drops_any_addendum():
    """An addendum only means something when continuing."""
    d = FakeDriver(LoopVerdict(verdict="done", reason="all wired",
                               addendum="stray text"))
    assert (await _judge(d)).addendum == ""


@pytest.mark.asyncio
async def test_a_raising_driver_continues():
    """The cap bounds runaway; a failed call must not end the loop."""
    got = await _judge(FakeDriver(raises=True))
    assert got.verdict == "continue"
    assert got.ok is False
    assert got.addendum == ""


@pytest.mark.asyncio
async def test_an_unparseable_payload_continues():
    got = await _judge(FakeDriver(value=None))
    assert got.verdict == "continue"
    assert got.ok is False


@pytest.mark.asyncio
async def test_an_unknown_verdict_string_continues():
    """A model that invents a fourth verdict must not stop the loop."""
    got = await _judge(FakeDriver(LoopVerdict.model_construct(
        verdict="maybe", reason="?", addendum="")))
    assert got.verdict == "continue"


@pytest.mark.asyncio
async def test_the_prompt_carries_the_facts_and_the_iteration():
    d = FakeDriver(LoopVerdict(verdict="continue", reason="x"))
    await _judge(d)
    joined = "\n".join(d.calls[0][1])
    assert "a1" in joined                     # the commit
    assert "3" in joined and "20" in joined   # iteration N/max


@pytest.mark.asyncio
async def test_the_still_streak_is_stated_not_inferred():
    d = FakeDriver(LoopVerdict(verdict="stuck", reason="no movement"))
    got = await _judge(d, still_streak=3)
    assert "3" in "\n".join(d.calls[0][1])
    assert got.verdict == "stuck"


@pytest.mark.asyncio
async def test_the_agents_stop_request_is_presented_as_a_claim():
    """Advisory, not authoritative — the burn this whole feature exists
    for was an agent stopping at iteration 1 of 20."""
    d = FakeDriver(LoopVerdict(verdict="continue", reason="UI unbuilt"))
    await _judge(d, advisory="I wired both rails and verified them")
    joined = "\n".join(d.calls[0][1])
    assert "I wired both rails" in joined
    assert "claim" in joined.lower() or "asked to stop" in joined.lower()


@pytest.mark.asyncio
async def test_no_advisory_means_no_stop_request_in_the_prompt():
    d = FakeDriver(LoopVerdict(verdict="continue", reason="x"))
    await _judge(d, advisory="")
    joined = "\n".join(d.calls[0][1]).lower()
    assert "asked to stop" not in joined
