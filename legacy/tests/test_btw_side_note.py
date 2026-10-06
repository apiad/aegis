"""`/btw` end to end, minus the API call: window in, side note out.

The generate() call itself is exercised in test_oneshot_generate.py (and
live-marked there). Here the driver is a stub, so these tests are about
what /btw does with what it gets back — including when it gets nothing.
"""
from __future__ import annotations

import pytest

from aegis.btw import BtwAnswer, side_note
from aegis.drivers.oneshot import Generation
from aegis.events import AssistantText, Result, UserMessage
from aegis.state.session_log import EventReplay


class StubDriver:
    """A driver that returns a canned Generation and records its prompt."""
    supports_oneshot = True

    def __init__(self, generation: Generation | None = None,
                 boom: bool = False) -> None:
        self.generation = generation or Generation(
            value=BtwAnswer(answer="core/manager.py", needs_more=False),
            model="haiku", duration_ms=5200, cost_usd=0.0044)
        self.boom = boom
        self.instructions: list[str] = []
        self.cwd: str = ""

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        if self.boom:
            raise RuntimeError("subprocess exploded")
        self.instructions = list(instructions)
        self.cwd = cwd
        return self.generation


def replay_of(*events) -> EventReplay:
    return EventReplay(events=list(events), interrupted=False)


CONVO = replay_of(
    UserMessage(text="where did the fork guard end up?"),
    AssistantText(text="core/manager.py, in _forkable()"),
    Result(duration_ms=10, is_error=False),
)


async def run(prompt="which file holds the fork guard?", *,
              replay=CONVO, driver=None, agent=None, cwd="/tmp"):
    return await side_note(prompt, replay=replay,
                           driver=driver or StubDriver(),
                           agent=agent, cwd=cwd)


# ---------- the answer ---------------------------------------------------

async def test_returns_the_models_answer():
    note = await run()
    assert note.ok
    assert note.answer == "core/manager.py"


async def test_carries_the_price_of_the_call():
    """A side note is a paid call and the price should be visible."""
    note = await run()
    assert note.model == "haiku"
    assert note.duration_ms == 5200
    assert note.cost_usd == pytest.approx(0.0044)


async def test_carries_the_window_header():
    note = await run()
    assert note.header == "all 1 turn"


# ---------- what the model is actually asked -----------------------------

async def test_the_window_is_handed_to_the_model():
    driver = StubDriver()
    await run(driver=driver)
    joined = "\n".join(driver.instructions)
    assert "where did the fork guard end up?" in joined
    assert "core/manager.py, in _forkable()" in joined


async def test_the_question_is_handed_to_the_model():
    driver = StubDriver()
    await run("which file holds the fork guard?", driver=driver)
    assert "which file holds the fork guard?" in "\n".join(driver.instructions)


async def test_the_model_is_told_the_window_is_a_slice():
    """The header goes to the model too, not just the reader — a model that
    does not know it is seeing a slice cannot set needs_more honestly."""
    long_convo = []
    for i in range(40):
        long_convo += [UserMessage(text=f"q{i}"),
                       AssistantText(text=f"a{i}"),
                       Result(duration_ms=1, is_error=False)]
    driver = StubDriver()
    await run(replay=replay_of(*long_convo), driver=driver)
    assert "of 40 turns" in "\n".join(driver.instructions)


# ---------- needs_more ---------------------------------------------------

async def test_needs_more_is_reported_when_the_model_sets_it():
    driver = StubDriver(Generation(
        value=BtwAnswer(answer="not in the window", needs_more=True),
        model="haiku"))
    note = await run(driver=driver)
    assert note.needs_more


async def test_needs_more_is_false_by_default():
    assert not (await run()).needs_more


# ---------- failure must never disturb the conversation ------------------

async def test_a_driver_explosion_is_a_failed_note_not_an_exception():
    note = await run(driver=StubDriver(boom=True))
    assert not note.ok
    assert note.error
    assert not note.answer


async def test_an_unparseable_answer_is_a_failed_note():
    note = await run(driver=StubDriver(Generation(value=None, model="haiku")))
    assert not note.ok
    assert note.error


async def test_an_empty_conversation_still_answers():
    """A brand-new tab has no turns. /btw should still work — the model
    just gets an empty window and can say so."""
    note = await run(replay=replay_of())
    assert note.ok
