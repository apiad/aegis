"""A restarted daemon used to draw every card nearly empty: the last recap,
the tool tail, the cost and the context gauge all lived only in memory.
Operator ruling 2026-09-16: rebuild them from the transcript on resume, and
recap every turn, because the card should always say what the last turn did.
"""

import pytest

from aegis.digest.models import TurnFacts
from aegis.events import (
    AssistantText,
    RecapNote,
    Result,
    TokenUsage,
    ToolUse,
    UserMessage,
)
from aegis.fleet.models import CardView, EventLine
from aegis.fleet.render import render_item
from aegis.recap import Recap
from aegis.state.event_codec import decode_event, encode_event
from aegis.state.session_log import replay_events
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield Result(duration_ms=1, is_error=False)


@pytest.fixture
def session(tmp_path):
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
        make_session=lambda p, u, h, **kw: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    s = mgr._sync_spawn("opus")
    s.recap_enabled = True
    return s


def _facts(facts):
    async def _build(**_kw):
        return facts

    return _build


def _recap_returning(line, calls):
    async def _fake(**kw):
        calls.append(kw)
        return Recap(line=line, ok=True)

    return _fake


U1 = TokenUsage(input=10, cache_creation=1_000, cache_read=20_000, output=5)
U2 = TokenUsage(input=40, cache_creation=2_000, cache_read=90_000, output=900)


def _transcript(with_note: bool):
    evs = [
        UserMessage(text="fix the pusher"),
        ToolUse(name="Edit", summary="apps/sigere/pusher.py", usage=U1),
        ToolUse(name="Grep", summary="child call", parent_tool_use_id="t1"),
        AssistantText(text="done", usage=U1),
        Result(duration_ms=5, is_error=False, usage=U2),
    ]
    if with_note:
        evs.append(RecapNote(line="Rewrote pusher.py retry loop."))
    return evs, [100.0 + i for i in range(len(evs))]


# ---- the record ---------------------------------------------------------


def test_a_recap_note_round_trips_through_the_codec():
    ev = RecapNote(line="Wrote 3 tests.")
    assert decode_event(encode_event(ev)) == ev


def test_a_recap_note_never_enters_the_recap_window():
    """Summaries must not compound: a persisted recap would otherwise be
    read by the next recap as part of the conversation."""
    from aegis.btw.window import assemble

    evs, _ = _transcript(with_note=True)
    assert "Rewrote pusher.py" not in assemble(evs).text


# ---- every turn recaps, only a moving turn draws --------------------------


@pytest.mark.asyncio
async def test_a_read_only_turn_recaps_the_card_but_draws_nothing(
    session, monkeypatch
):
    calls, drawn = [], []
    monkeypatch.setattr(
        "aegis.core.session.recap_for", _recap_returning("Explained the gate.", calls)
    )
    monkeypatch.setattr(session.digest, "build", _facts(TurnFacts(assistant_tail="x")))
    session.add_recap_observer(lambda _s, r: drawn.append(r))
    await session.send_and_wait("what does the gate do?")
    await session._recap_task
    assert session._last_recap_line == "Explained the gate."
    assert drawn == []


@pytest.mark.asyncio
async def test_every_recap_is_written_to_the_session_log(session, monkeypatch):
    monkeypatch.setattr(
        "aegis.core.session.recap_for", _recap_returning("Explained the gate.", [])
    )
    monkeypatch.setattr(session.digest, "build", _facts(TurnFacts()))
    await session.send_and_wait("hi")
    await session._recap_task
    notes = [
        e
        for e in replay_events(session.state_dir, session.log_id).events
        if isinstance(e, RecapNote)
    ]
    assert notes == [RecapNote(line="Explained the gate.")]


# ---- the card, rebuilt on resume ------------------------------------------


def test_resume_restores_the_last_recap_tools_cost_and_context(session, monkeypatch):
    calls = []
    monkeypatch.setattr("aegis.core.session.recap_for", _recap_returning("x", calls))
    evs, stamps = _transcript(with_note=True)
    session.rehydrate_card(evs, stamps)

    assert session._last_recap_line == "Rewrote pusher.py retry loop."
    # The subagent's call is the subagent working, as on the live path.
    assert session.recent_events == (
        EventLine(at=101.0, tool="Edit", summary="apps/sigere/pusher.py"),
    )
    assert session.metrics.c_out == U2.output
    assert session.metrics.c_in == U2.true_input
    # The gauge takes the turn's peak sub-turn context, not the summed Result.
    assert session.metrics.last_true_input == U1.true_input
    assert calls == []  # a persisted recap costs nothing


def test_resume_twice_does_not_double_count(session):
    evs, stamps = _transcript(with_note=True)
    session.rehydrate_card(evs, stamps)
    session.rehydrate_card(evs, stamps)  # a second view attaching
    assert session.metrics.c_out == U2.output
    assert len(session.recent_events) == 1


@pytest.mark.asyncio
async def test_a_session_that_already_ran_a_turn_is_not_rehydrated(
    session, monkeypatch
):
    """A view attaching after the brain ran turns would add the log's totals
    on top of the live ones."""
    monkeypatch.setattr("aegis.core.session.recap_for", _recap_returning("x", []))
    monkeypatch.setattr(session.digest, "build", _facts(TurnFacts()))
    await session.send_and_wait("hi")
    session._cancel_recap()
    before = session.metrics.c_out
    evs, stamps = _transcript(with_note=True)
    session.rehydrate_card(evs, stamps)
    assert session.metrics.c_out == before
    assert session.recent_events == ()


@pytest.mark.asyncio
async def test_a_log_without_a_recap_pays_for_one_and_keeps_it(session, monkeypatch):
    calls, drawn = [], []
    monkeypatch.setattr(
        "aegis.core.session.recap_for", _recap_returning("Rebuilt from log.", calls)
    )
    session.add_recap_observer(lambda _s, r: drawn.append(r))
    evs, stamps = _transcript(with_note=False)
    session.rehydrate_card(evs, stamps)
    await session._recap_task

    assert len(calls) == 1
    assert session._last_recap_line == "Rebuilt from log."
    assert drawn == []  # not a new turn: nothing is drawn in the transcript
    logged = replay_events(session.state_dir, session.log_id).events
    assert RecapNote(line="Rebuilt from log.") in logged


@pytest.mark.asyncio
async def test_a_log_with_no_finished_turn_pays_nothing(session, monkeypatch):
    calls = []
    monkeypatch.setattr("aegis.core.session.recap_for", _recap_returning("x", calls))
    session.rehydrate_card([UserMessage(text="hi")], [1.0])
    assert session._recap_task is None
    assert calls == []


# ---- the card draws the recap, not the commands ---------------------------


def test_a_card_with_a_recap_hides_the_command_tail():
    ev = EventLine(at=0.0, tool="Bash", summary="SENTINEL-CMD")
    assert "SENTINEL-CMD" not in render_item(CardView(handle="a", did="landed x", events=(ev,)), C, 0).plain


def test_a_card_without_a_recap_falls_back_to_the_commands():
    ev = EventLine(at=0.0, tool="Bash", summary="SENTINEL-CMD")
    assert "SENTINEL-CMD" in render_item(CardView(handle="a", events=(ev,)), C, 0).plain


def test_disabled_recaps_are_never_paid_for_on_resume(session, monkeypatch):
    calls = []
    monkeypatch.setattr("aegis.core.session.recap_for", _recap_returning("x", calls))
    session.recap_enabled = False
    evs, stamps = _transcript(with_note=False)
    session.rehydrate_card(evs, stamps)
    assert session._recap_task is None


@pytest.mark.asyncio
async def test_every_resume_path_rebuilds_the_card_through_the_pane():
    """Boot, reopen and reattach all build a ConversationPane with a replay;
    the card is rebuilt there, next to the plan."""
    from pathlib import Path

    from aegis.state.session_log import EventReplay
    from aegis.tui.pane import ConversationPane
    from tests.test_pane_windowing import _agent, _app

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        core = app._active._core
        replay = EventReplay(
            events=[
                ToolUse(name="Edit", summary="restored.py"),
                RecapNote(line="Restored line."),
            ],
            interrupted=False,
            stamps=[1.0, 2.0],
        )
        ConversationPane(None, _agent(), "default", "restored", app._palette,
                         core=core, replay=replay, project_root=Path.cwd())
        assert core._last_recap_line == "Restored line."
        assert [e.summary for e in core.recent_events] == ["restored.py"]


@pytest.mark.asyncio
async def test_a_turn_with_recaps_off_pays_nothing(session, monkeypatch):
    calls = []
    monkeypatch.setattr("aegis.core.session.recap_for", _recap_returning("x", calls))
    monkeypatch.setattr(session.digest, "build", _facts(TurnFacts()))
    session.recap_enabled = False
    await session.send_and_wait("hi")
    assert session._recap_task is None
    assert calls == []


def test_the_gauge_reads_the_last_turn_not_an_earlier_result(session):
    """A Result's usage sums every sub-turn; fed to the gauge it would carry
    into the next turn's peak, as the live path is careful not to."""
    evs = [
        AssistantText(text="a", usage=U1),
        Result(duration_ms=1, is_error=False, usage=U2),
        AssistantText(text="b", usage=U1),
        Result(duration_ms=1, is_error=False, usage=U1),
        RecapNote(line="x"),
    ]
    session.rehydrate_card(evs, [0.0] * len(evs))
    assert session.metrics.last_true_input == U1.true_input
