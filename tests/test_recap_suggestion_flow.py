"""The drafted reply reaches a view on every turn, including the quiet ones.

The recap's draw gate keeps a conversation of pure questions free of repeated
transcript blocks, and it stays. But a turn that moved nothing is exactly the
turn that ended on a question, so the suggestion rides its own observer.
"""

import pytest

from aegis.digest.models import TurnFacts
from aegis.events import RecapNote, Result
from aegis.recap import Recap
from aegis.state.session_log import replay_events


class _Harness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield Result(duration_ms=1, is_error=False)


def _brain(tmp_path):
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    roster = {
        "opus": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    mgr = make_brain(
        roster,
        "opus",
        make_session=lambda p, u, h, **kw: _Harness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    s = mgr._sync_spawn("opus")
    s.recap_enabled = True
    return s


async def _turn(s, monkeypatch):
    async def build(**_kw):
        return TurnFacts()  # moved == False, so the recap is never drawn

    monkeypatch.setattr(s.digest, "build", build)
    await s.send_and_wait("go")
    if s._recap_task is not None:
        await s._recap_task


def test_recapnote_carries_a_suggestion_and_old_records_decode():
    assert RecapNote(line="x").suggestion == ""
    assert RecapNote(line="x", suggestion="go on").suggestion == "go on"


def test_the_codec_round_trips_the_suggestion():
    """The dataclass default is not enough: the wire form is written and
    read field by field, and a field missing there persists as empty."""
    from aegis.state.event_codec import decode_event, encode_event

    note = RecapNote(line="x", attention="needs_input", task="t", suggestion="go on")
    assert decode_event(encode_event(note)) == note
    # A record written before the field existed still reads.
    assert decode_event({"t": "RecapNote", "line": "x"}).suggestion == ""


@pytest.mark.asyncio
async def test_the_suggestion_is_emitted_and_persisted(tmp_path, monkeypatch):
    s = _brain(tmp_path)
    seen = []
    s.add_suggestion_observer(lambda _s, text: seen.append(text))

    async def fake(**_kw):
        return Recap(
            line="Asked about the gate.",
            task="Ship it",
            suggestion="one call",
            attention="done",
            ok=True,
        )

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    assert seen[-1] == "one call"
    assert s.suggestion == "one call"
    notes = [
        e
        for e in replay_events(s.state_dir, s.log_id).events
        if isinstance(e, RecapNote)
    ]
    assert notes[-1].suggestion == "one call"


@pytest.mark.asyncio
async def test_a_repeated_recap_still_carries_a_fresh_suggestion(
    tmp_path, monkeypatch
):
    """A recap that agrees with the hard category and moved nothing is
    never drawn — not on the first turn and not on the second, where the
    identity guard returns before the draw is even considered.

    That is the whole point of the guard, and it is also the turn most
    likely to want a suggestion — so the emit sits in front of it.
    """
    s = _brain(tmp_path)
    drawn, seen = [], []
    s.add_recap_observer(lambda _s, r: drawn.append(r.line))
    s.add_suggestion_observer(lambda _s, text: seen.append(text))
    suggestions = iter(["one call", "do it now"])

    async def fake(**_kw):
        return Recap(
            line="Asked about the gate.",
            task="Ship it",
            suggestion=next(suggestions),
            attention="done",
            ok=True,
        )

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    await _turn(s, monkeypatch)

    # Neither recap was drawn: nothing moved and the category agreed...
    assert drawn == []
    # ...but its suggestion arrived anyway. "" is the clear at each turn start.
    assert seen == ["one call", "", "do it now"]


@pytest.mark.asyncio
async def test_a_new_turn_clears_a_stale_suggestion(tmp_path, monkeypatch):
    """A suggestion drafted for the turn before last is worse than none."""
    s = _brain(tmp_path)
    seen = []

    async def fake(**_kw):
        return Recap(line="x", task="t", suggestion="one call", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    s.add_suggestion_observer(lambda _s, text: seen.append(text))
    assert s.suggestion == "one call"

    await _turn(s, monkeypatch)
    # Cleared when the turn started, before the new recap landed.
    assert seen[0] == ""


@pytest.mark.asyncio
async def test_an_empty_suggestion_is_not_re_emitted(tmp_path, monkeypatch):
    """Most turns have no suggestion; they must not wake every view."""
    s = _brain(tmp_path)
    seen = []
    s.add_suggestion_observer(lambda _s, text: seen.append(text))

    async def fake(**_kw):
        return Recap(line="x", task="t", suggestion="", attention="done", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    await _turn(s, monkeypatch)
    assert seen == []


@pytest.mark.asyncio
async def test_removing_the_observer_stops_the_calls(tmp_path, monkeypatch):
    s = _brain(tmp_path)
    seen = []

    def cb(_s, text):
        seen.append(text)

    s.add_suggestion_observer(cb)
    s.remove_suggestion_observer(cb)
    s.remove_suggestion_observer(cb)  # idempotent

    async def fake(**_kw):
        return Recap(line="x", task="t", suggestion="one call", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    assert seen == []
