"""The session keeps the recap's task and next, and hands the task back.

Without the previous task in the prompt, `task` wanders between phrasings of
the same goal on consecutive turns; a restart that forgot it would reopen
that drift.
"""

import pytest

from aegis.digest.models import TurnFacts
from aegis.events import RecapNote, Result
from aegis.recap import Recap
from aegis.state.event_codec import decode_event, encode_event
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
        return TurnFacts()

    monkeypatch.setattr(s.digest, "build", build)
    await s.send_and_wait("go")
    if s._recap_task is not None:
        await s._recap_task


@pytest.mark.asyncio
async def test_a_turn_keeps_task_and_next_and_hands_the_task_back(
    tmp_path, monkeypatch
):
    s = _brain(tmp_path)
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return Recap(
            line="Fixed it.", task="Ship Help", next="Build.", attention="done", ok=True
        )

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    assert calls[0]["previous_task"] == ""
    assert s._last_recap_task == "Ship Help"
    assert s._last_recap_next == "Build."
    notes = [
        e
        for e in replay_events(s.state_dir, s.log_id).events
        if isinstance(e, RecapNote)
    ]
    assert notes[-1] == RecapNote(
        line="Fixed it.", attention="done", task="Ship Help", next="Build."
    )
    await _turn(s, monkeypatch)
    assert calls[1]["previous_task"] == "Ship Help"


@pytest.mark.asyncio
async def test_a_changed_task_on_the_same_line_is_news(tmp_path, monkeypatch):
    s = _brain(tmp_path)
    tasks = iter(["Ship Help", "Ship Docs"])

    async def fake(**_kw):
        return Recap(line="Fixed it.", task=next(tasks), attention="done", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    await _turn(s, monkeypatch)
    await _turn(s, monkeypatch)
    assert s._last_recap_task == "Ship Docs"
    notes = [
        e
        for e in replay_events(s.state_dir, s.log_id).events
        if isinstance(e, RecapNote)
    ]
    assert [n.task for n in notes] == ["Ship Help", "Ship Docs"]


def test_a_restart_restores_task_and_next(tmp_path):
    s = _brain(tmp_path)
    s.rehydrate_card(
        [
            Result(duration_ms=1, is_error=False),
            RecapNote(line="x", attention="done", task="T", next="N"),
        ],
        [1.0, 2.0],
    )
    assert s._last_recap_task == "T"
    assert s._last_recap_next == "N"


def test_a_stale_note_restores_the_task_but_not_next(tmp_path):
    # The identity guard skips a note when outcome, task and category repeat,
    # so a Result after the last note is common; the goal outlives the turn.
    s = _brain(tmp_path)
    s.recap_enabled = False  # no paid resume recap for this check
    s.rehydrate_card(
        [
            RecapNote(line="x", attention="done", task="T", next="N"),
            Result(duration_ms=1, is_error=False),
        ],
        [1.0, 2.0],
    )
    assert s._last_recap_task == "T"
    assert s._last_recap_next == ""


@pytest.mark.asyncio
async def test_a_changed_next_alone_is_kept_but_not_drawn(tmp_path, monkeypatch):
    s = _brain(tmp_path)
    nexts = iter(["Build.", "Test."])

    async def fake(**_kw):
        return Recap(
            line="Fixed it.", task="Ship Help", next=next(nexts), attention="done",
            ok=True,
        )

    monkeypatch.setattr("aegis.core.session.recap_for", fake)
    # Every turn asks to be drawn, so a fall-through past the guard would draw.
    monkeypatch.setattr("aegis.core.session.should_draw_recap", lambda _f: True)
    await _turn(s, monkeypatch)
    emitted = []
    monkeypatch.setattr(s, "_emit_recap", emitted.append)
    await _turn(s, monkeypatch)
    assert s._last_recap_next == "Test."
    assert emitted == []
    notes = [
        e
        for e in replay_events(s.state_dir, s.log_id).events
        if isinstance(e, RecapNote)
    ]
    assert [n.next for n in notes] == ["Build.", "Test."]


def test_an_old_note_decodes_with_empty_task_and_next():
    assert decode_event({"t": "RecapNote", "line": "x"}) == RecapNote(line="x")


def test_a_note_round_trips_all_four_fields():
    ev = RecapNote(line="x", attention="needs_input", task="T", next="N")
    assert decode_event(encode_event(ev)) == ev


@pytest.mark.asyncio
async def test_the_mid_turn_recap_gets_the_previous_task(tmp_path, monkeypatch):
    s = _brain(tmp_path)
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return Recap()

    async def build(**_kw):
        return TurnFacts()

    monkeypatch.setattr("aegis.core.session.recap_in_flight_for", fake)
    monkeypatch.setattr(s.digest, "build", build)
    s._last_recap_task = "Ship Help"
    await s._run_fleet_recap()
    assert calls[0]["previous_task"] == "Ship Help"
