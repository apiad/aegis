"""The category rides the turn recap and survives in the log."""

from aegis.events import RecapNote
from aegis.recap import SYSTEM, Recap, StandingRecap, recap_turn
from aegis.state.event_codec import decode_event, encode_event


class _Gen:
    def __init__(self, value):
        self.value, self.model, self.duration_ms, self.cost_usd = value, "haiku", 1, 0.001


class _Driver:
    def __init__(self, value):
        self._value = value
        self.system = None

    async def generate_detailed(self, agent, cwd, schema, system, *rest):
        self.system = system
        return _Gen(self._value)


async def test_the_model_category_reaches_the_recap():
    from aegis.digest.models import TurnFacts

    d = _Driver(
        StandingRecap(task="t", outcome="Asked which fields ship.", next="", attention="needs_input")
    )
    got = await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert got.ok and got.attention == "needs_input"
    assert d.system.startswith(SYSTEM) and "needs_input" in SYSTEM


def test_the_schema_rejects_an_unknown_category():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        StandingRecap(task="t", outcome="x", next="", attention="urgent")


def test_a_recap_without_a_category_is_empty_not_done():
    assert Recap(line="x", ok=True).attention == ""


def test_the_note_round_trips_its_category():
    ev = RecapNote(line="Wrote 3 tests.", attention="review")
    assert decode_event(encode_event(ev)) == ev


def test_an_old_note_without_a_category_decodes_as_done():
    assert decode_event({"t": "RecapNote", "line": "x"}) == RecapNote(line="x", attention="done")
