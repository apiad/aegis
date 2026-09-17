"""The recap's generation half, against a fake driver."""
import pytest

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation
from aegis.recap import (
    SESSION_WINDOW, TURN_WINDOW, Recap, StandingRecap, recap_session, recap_turn,
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
    d = FakeDriver(StandingRecap(task="Write the judge spec",
                                 outcome="Wrote the spec.", next="Wire it.",
                                 attention="done"))
    got = await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                           agent=object(), cwd=".")
    assert got.ok is True
    assert got.line == "Wrote the spec."
    assert got.text == "Wrote the spec."
    assert got.task == "Write the judge spec"
    assert got.next == "Wire it."


@pytest.mark.asyncio
async def test_turn_recap_asks_for_the_one_schema():
    d = FakeDriver(StandingRecap(task="t", outcome="x", next="", attention="done"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    schema, _ = d.calls[0]
    assert schema is StandingRecap


@pytest.mark.asyncio
async def test_the_facts_are_in_the_prompt():
    """The guard against claiming work that did not happen."""
    d = FakeDriver(StandingRecap(task="t", outcome="x", next="", attention="done"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    _, instructions = d.calls[0]
    assert any("51430de" in part for part in instructions)


@pytest.mark.asyncio
async def test_session_recap_returns_the_block():
    d = FakeDriver(StandingRecap(task="the judge", outcome="the spec",
                                 next="the wiring", attention="waiting"))
    got = await recap_session(replay=FakeReplay(), facts=FACTS, driver=d,
                              agent=object(), cwd=".")
    assert got.ok is True
    assert (got.task, got.line, got.next) == ("the judge", "the spec", "the wiring")
    assert "the judge" in got.block and "the wiring" in got.block


@pytest.mark.asyncio
async def test_the_turn_and_session_recaps_differ_only_in_window(monkeypatch):
    import aegis.recap as recap

    seen = []
    real = recap.assemble
    monkeypatch.setattr(recap, "assemble",
                        lambda replay, **opts: seen.append(opts) or real(replay, **opts))
    v = StandingRecap(task="t", outcome="x", next="", attention="done")
    d = FakeDriver(v)
    await recap_turn(replay=[], facts=FACTS, driver=d, agent=object(), cwd=".")
    await recap_session(replay=[], facts=FACTS, driver=d, agent=object(), cwd=".")
    assert seen == [TURN_WINDOW, SESSION_WINDOW]
    assert d.calls[0][1][0] == d.calls[1][1][0]


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


def test_the_session_block_renders_as_separate_lines():
    """It goes through rich Markdown, which collapses single newlines.

    A plain newline join drew as one run-on paragraph — "task: x
    outcome: y next: z" — while every substring assertion still passed.
    Assert on the RENDERED output, not on `block`.
    """
    from rich.console import Console

    from aegis.render import render_recap
    from aegis.tui.themes import INK, aegis_colors

    r = Recap(task="the judge", line="the spec", next="the wiring", ok=True)
    console = Console(width=76, no_color=True)
    with console.capture() as cap:
        console.print(render_recap(r, aegis_colors(INK), session=True))
    body = [ln.strip() for ln in cap.get().splitlines() if ln.strip()]
    # One line each for task / outcome / next, not one paragraph.
    assert sum("the judge" in ln for ln in body) == 1
    joined = [ln for ln in body if "the judge" in ln][0]
    assert "the spec" not in joined and "the wiring" not in joined
