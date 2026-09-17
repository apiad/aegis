"""The session ends each turn with a category, from the model and the facts."""

import pytest

from aegis.digest.models import TurnFacts
from aegis.events import RecapNote, Result
from aegis.recap import Recap
from aegis.state.session_log import replay_events


class _Harness:
    def __init__(self, error=False):
        self.error = error

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield Result(duration_ms=1, is_error=self.error)


def _brain(tmp_path, harness, **spawn):
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    roster = {"opus": Agent(harness="claude-code", model="opus", effort="high", permission="auto")}
    mgr = make_brain(roster, "opus", make_session=lambda p, u, h, **kw: harness,
                     mcp=None, roots=AegisRoots.for_project(tmp_path))
    s = mgr._sync_spawn("opus", **spawn)
    s.recap_enabled = True
    return mgr, s


def _model_says(monkeypatch, attention, line="did a thing"):
    async def fake(**_kw):
        return Recap(line=line, attention=attention, ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)


async def _turn(s, monkeypatch, facts=TurnFacts()):
    async def build(**_kw):
        return facts

    monkeypatch.setattr(s.digest, "build", build)
    await s.send_and_wait("go")
    if s._recap_task is not None:
        await s._recap_task


@pytest.mark.asyncio
async def test_the_model_category_lands_on_the_session(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)
    assert s.attention == "needs_input"
    assert s.attention_seq >= 1


@pytest.mark.asyncio
async def test_an_error_result_wins_over_the_model(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness(error=True))
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert s.attention == "error"


@pytest.mark.asyncio
async def test_a_failed_recap_still_sets_the_hard_category(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness(error=True))

    async def refused(**_kw):
        return Recap(error="no text_generation")

    monkeypatch.setattr("aegis.core.session.recap_for", refused)
    await _turn(s, monkeypatch)
    assert s.attention == "error"


@pytest.mark.asyncio
async def test_a_queue_worker_never_needs_the_operator(tmp_path, monkeypatch):
    from aegis.fleet.models import Origin

    _, s = _brain(tmp_path, _Harness(), origin=Origin(kind="queue", by="tasks"))
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)
    assert s.attention == "done"


@pytest.mark.asyncio
async def test_a_live_wait_makes_it_waiting_until_the_wait_ends(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    waiting = [True]
    s.wait_probe = lambda: waiting[0]
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert s.attention == "waiting"
    assert s.effective_attention == "waiting"
    waiting[0] = False
    assert s.effective_attention == "done"


@pytest.mark.asyncio
async def test_a_non_done_recap_is_drawn_even_when_nothing_moved(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    drawn = []
    s.add_recap_observer(lambda _s, r: drawn.append(r))
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)  # TurnFacts() did not move
    assert [r.attention for r in drawn] == ["needs_input"]


@pytest.mark.asyncio
async def test_a_done_recap_is_not_drawn_when_nothing_moved(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    drawn = []
    s.add_recap_observer(lambda _s, r: drawn.append(r))
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert drawn == []


@pytest.mark.asyncio
async def test_the_category_is_persisted_and_restored(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "review")
    await _turn(s, monkeypatch)
    notes = [e for e in replay_events(s.state_dir, s.log_id).events if isinstance(e, RecapNote)]
    assert notes[-1].attention == "review"

    _, fresh = _brain(tmp_path, _Harness())
    fresh.rehydrate_card([Result(duration_ms=1, is_error=False), notes[-1]], [1.0, 2.0])
    assert fresh.attention == "review"
    assert fresh.attention_seq >= 1


def test_the_manager_probe_sees_a_live_monitor(tmp_path):
    mgr, s = _brain(tmp_path, _Harness())
    assert mgr.waits_on(s.handle) is False

    class _MM:
        def snapshot(self, *, for_handle=None):
            return [object()] if for_handle == s.handle else []

    mgr.monitor_manager = _MM()
    assert mgr.waits_on(s.handle) is True
    assert s.wait_probe() is True


def test_the_manager_probe_sees_a_working_child(tmp_path):
    from aegis.tui.state import AgentState

    mgr, s = _brain(tmp_path, _Harness())
    child = mgr._sync_spawn("opus", spawned_by=s.handle)
    child.state = AgentState.working
    assert mgr.waits_on(s.handle) is True
    child.state = AgentState.ready
    assert mgr.waits_on(s.handle) is False


def test_the_recap_schema_lists_exactly_the_categories():
    from typing import get_args

    from aegis.attention import CATEGORIES
    from aegis.recap import TurnRecap

    assert set(get_args(TurnRecap.model_fields["attention"].annotation)) == set(CATEGORIES)


def test_a_result_after_the_last_note_makes_the_note_stale(tmp_path):
    _, fresh = _brain(tmp_path, _Harness())
    fresh.recap_enabled = False
    fresh.rehydrate_card(
        [
            Result(duration_ms=1, is_error=False),
            RecapNote(line="which one?", attention="needs_input"),
            Result(duration_ms=1, is_error=True),
        ],
        [1.0, 2.0, 3.0],
    )
    assert fresh.attention == "error"


def test_an_answered_question_does_not_come_back_after_a_restart(tmp_path):
    _, fresh = _brain(tmp_path, _Harness())
    fresh.recap_enabled = False
    fresh.rehydrate_card(
        [
            RecapNote(line="which one?", attention="needs_input"),
            Result(duration_ms=1, is_error=False),
        ],
        [1.0, 2.0],
    )
    assert fresh.attention == "done"


def test_a_restored_done_is_not_pending_in_a_fresh_view(tmp_path):
    _, fresh = _brain(tmp_path, _Harness())
    fresh.rehydrate_card(
        [Result(duration_ms=1, is_error=False), RecapNote(line="x", attention="done")],
        [1.0, 2.0],
    )
    assert fresh.attention == "done"
    assert fresh.attention_seq == 0


def test_a_restored_question_is_still_pending_in_a_fresh_view(tmp_path):
    _, fresh = _brain(tmp_path, _Harness())
    fresh.rehydrate_card(
        [Result(duration_ms=1, is_error=False), RecapNote(line="x", attention="needs_input")],
        [1.0, 2.0],
    )
    assert fresh.attention_seq == 1


@pytest.mark.asyncio
async def test_a_recap_agreeing_with_the_hard_category_does_not_bump_again(
    tmp_path, monkeypatch
):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert s.attention == "done"
    assert s.attention_seq == 1


@pytest.mark.asyncio
async def test_the_resume_recap_is_never_drawn(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    drawn = []
    s.add_recap_observer(lambda _s, r: drawn.append(r))
    _model_says(monkeypatch, "needs_input")
    s.rehydrate_card([Result(duration_ms=1, is_error=False)], [1.0])
    await s._recap_task
    assert drawn == []
    assert s.attention == "needs_input"
    notes = [e for e in replay_events(s.state_dir, s.log_id).events if isinstance(e, RecapNote)]
    assert [n.attention for n in notes] == ["needs_input"]


@pytest.mark.asyncio
async def test_the_resume_recap_knows_the_last_turn_errored(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "done")
    s.rehydrate_card([Result(duration_ms=1, is_error=True)], [1.0])
    await s._recap_task
    assert s.attention == "error"


@pytest.mark.asyncio
async def test_the_hard_category_is_set_before_the_turn_end_state(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    s.attention = "needs_input"
    seen = []
    s.add_state_observer(
        lambda _s, state, finished: finished and seen.append(_s.attention)
    )
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert seen == ["done"]


@pytest.mark.asyncio
async def test_a_turn_with_no_result_is_an_error_before_the_state(tmp_path, monkeypatch):
    class _Silent(_Harness):
        async def events(self):
            return
            yield

    _, s = _brain(tmp_path, _Silent())
    seen = []
    s.add_state_observer(
        lambda _s, state, finished: finished and seen.append(_s.attention)
    )
    _model_says(monkeypatch, "done")

    async def build(**_kw):
        return TurnFacts()

    monkeypatch.setattr(s.digest, "build", build)
    await s._run_turn("go")  # send_and_wait would wait for a Result forever
    assert seen == ["error"]
