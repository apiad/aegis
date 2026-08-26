"""/btw sees what the session DID, not only what it said."""
import pytest

from aegis.btw import BtwAnswer, side_note
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation


class FakeDriver:
    supports_oneshot = True

    def __init__(self):
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append(instructions)
        return Generation(value=BtwAnswer(answer="yes"), model="haiku",
                          duration_ms=10, cost_usd=0.001)


class FakeReplay:
    events = []


FACTS = TurnFacts(repos=(RepoDelta(name="aegis",
                                   commits=(CommitLine("51430de",
                                                       "docs: spec"),)),))


@pytest.mark.asyncio
async def test_btw_prompt_carries_the_facts():
    """'did that commit land?' is answerable now. Previously it was
    answerable only from whatever git calls survived the 500-char clip."""
    d = FakeDriver()
    await side_note("did it land?", replay=FakeReplay(), driver=d,
                    agent=object(), cwd=".", facts=FACTS)
    assert any("51430de" in part for part in d.calls[0])


@pytest.mark.asyncio
async def test_the_question_stays_last():
    """The window and facts are context; the question is the ask. Burying
    it mid-prompt is how a side note starts answering the wrong thing."""
    d = FakeDriver()
    await side_note("did it land?", replay=FakeReplay(), driver=d,
                    agent=object(), cwd=".", facts=FACTS)
    assert "did it land?" in d.calls[0][-1]


@pytest.mark.asyncio
async def test_btw_still_works_without_facts():
    """Back-compat: every existing caller passes none."""
    d = FakeDriver()
    note = await side_note("hi", replay=FakeReplay(), driver=d,
                           agent=object(), cwd=".")
    assert note.ok is True
    assert not any("what this turn did" in part for part in d.calls[0])
