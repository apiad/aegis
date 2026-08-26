"""The recap's generation half, against a fake driver."""
import pytest

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation
from aegis.recap import (
    Recap, SessionRecap, TurnRecap, recap_session, recap_turn,
)


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
                          duration_ms=1200, cost_usd=0.02)


class FakeReplay:
    events = []


FACTS = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("51430de",
                                                       "docs: spec"),)),))


@pytest.mark.asyncio
async def test_turn_recap_returns_the_line():
    d = FakeDriver(TurnRecap(line="Wrote the spec; 1 commit."))
    got = await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                           agent=object(), cwd=".")
    assert got.ok is True
    assert got.line == "Wrote the spec; 1 commit."
    assert got.text == "Wrote the spec; 1 commit."


@pytest.mark.asyncio
async def test_turn_recap_asks_for_the_turn_schema():
    d = FakeDriver(TurnRecap(line="x"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    schema, _ = d.calls[0]
    assert schema is TurnRecap


@pytest.mark.asyncio
async def test_the_facts_are_in_the_prompt():
    """The whole reason the recap can say '1 commit' at all."""
    d = FakeDriver(TurnRecap(line="x"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    _, instructions = d.calls[0]
    assert any("51430de" in part for part in instructions)


@pytest.mark.asyncio
async def test_session_recap_returns_the_block():
    d = FakeDriver(SessionRecap(building="the judge", done="the spec",
                                remaining="the wiring"))
    got = await recap_session(replay=FakeReplay(), facts=FACTS, driver=d,
                              agent=object(), cwd=".")
    assert got.ok is True
    assert got.building == "the judge"
    assert "the judge" in got.text and "the wiring" in got.text
    assert got.line == ""


@pytest.mark.asyncio
async def test_a_raising_driver_returns_a_failed_recap():
    """Best-effort by contract — a recap must never disturb the turn."""
    got = await recap_turn(replay=FakeReplay(), facts=FACTS,
                           driver=FakeDriver(raises=True), agent=object(),
                           cwd=".")
    assert got.ok is False
    assert "RuntimeError" in got.error
    assert got.line == ""


@pytest.mark.asyncio
async def test_an_unparseable_payload_returns_a_failed_recap():
    got = await recap_turn(replay=FakeReplay(), facts=FACTS,
                           driver=FakeDriver(value=None), agent=object(),
                           cwd=".")
    assert got.ok is False
    assert got.error


def test_footer_carries_the_price():
    r = Recap(line="x", model="haiku", duration_ms=1200, cost_usd=0.02,
              ok=True)
    assert "haiku" in r.footer and "1.2s" in r.footer
