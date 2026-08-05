"""PlanTracker — plan state plus per-task working time.

Working time, not wall-clock. A session idles between turns: send an
agent off, a task goes in_progress, the turn ends, and the operator is in
another tab for forty minutes. Wall-clock would report 41 minutes for one
minute of work. Elapsed accrues only while the session is mid-turn.

Every method takes an explicit ts. The tracker never reads a clock — that
is what makes a replayed log reproduce the live numbers exactly.
"""
import asyncio

import pytest

from aegis.core.session import AgentSession
from aegis.events import AgentPlan, PlanEntry, Result
from aegis.plan import PlanTracker
from aegis.tui.state import AgentState


def plan(*pairs, parent=None):
    return AgentPlan(
        entries=tuple(PlanEntry(content=c, status=s) for c, s in pairs),
        parent_tool_use_id=parent)


# -- shape -----------------------------------------------------------

def test_total_done_and_current():
    t = PlanTracker()
    t.apply_plan(plan(("a", "completed"), ("b", "in_progress"),
                      ("c", "pending")), ts=0.0)
    st = t.snapshot(ts=0.0)
    assert (st.done, st.total) == (1, 3)
    assert st.current.subject == "b"


def test_an_empty_plan_is_falsy():
    assert not PlanTracker().snapshot(ts=0.0)


def test_active_form_is_preferred_as_the_label_while_in_progress():
    t = PlanTracker()
    t.apply_plan(AgentPlan(entries=(
        PlanEntry(content="Write the spec", status="in_progress",
                  active_form="Writing the spec"),)), ts=0.0)
    assert t.snapshot(ts=0.0).tasks[0].label == "Writing the spec"


def test_subject_is_the_label_once_completed():
    t = PlanTracker()
    t.apply_plan(AgentPlan(entries=(
        PlanEntry(content="Write the spec", status="completed",
                  active_form="Writing the spec"),)), ts=0.0)
    assert t.snapshot(ts=0.0).tasks[0].label == "Write the spec"


# -- working time ----------------------------------------------------

def test_working_time_accrues_only_while_mid_turn():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    t.set_working(False, ts=10.0)          # turn ends, 10s of work
    st = t.snapshot(ts=1000.0)             # long idle gap
    assert st.tasks[0].working_s == 10.0


def test_idle_gap_contributes_nothing():
    t = PlanTracker()
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)   # never working
    assert t.snapshot(ts=500.0).tasks[0].working_s == 0.0


def test_re_entering_in_progress_resumes_rather_than_restarts():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    t.apply_plan(plan(("a", "completed")), ts=6.0)     # 6s banked
    t.apply_plan(plan(("a", "in_progress")), ts=6.0)   # reopened
    assert t.snapshot(ts=10.0).tasks[0].working_s == 10.0   # not 4.0


def test_never_started_task_reports_none_not_zero():
    """"Instant" and "never tracked" must not look alike — None renders
    as an em dash, 0.0 renders as 0:00."""
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "pending")), ts=0.0)
    assert t.snapshot(ts=9.0).tasks[0].working_s is None


def test_task_completed_without_ever_being_in_progress_reports_none():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "completed")), ts=0.0)
    assert t.snapshot(ts=9.0).tasks[0].working_s is None


def test_live_snapshot_includes_time_since_the_last_transition():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    assert t.snapshot(ts=4.0).tasks[0].working_s == 4.0


def test_snapshot_does_not_mutate_the_accumulator():
    """Reading the live figure repeatedly must not compound it."""
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    assert t.snapshot(ts=4.0).tasks[0].working_s == 4.0
    assert t.snapshot(ts=4.0).tasks[0].working_s == 4.0
    assert t.snapshot(ts=8.0).tasks[0].working_s == 8.0


def test_only_the_in_progress_task_accrues():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress"), ("b", "pending")), ts=0.0)
    st = t.snapshot(ts=5.0)
    assert st.tasks[0].working_s == 5.0
    assert st.tasks[1].working_s is None


def test_repeated_set_working_true_does_not_double_count():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress")), ts=0.0)
    t.set_working(True, ts=3.0)            # redundant, must be a no-op
    assert t.snapshot(ts=6.0).tasks[0].working_s == 6.0


# -- identity --------------------------------------------------------

def test_ids_track_a_task_across_a_subject_rename():
    """Task* carries stable ids, so renaming must not orphan the timing."""
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(AgentPlan(entries=(
        PlanEntry(content="old name", status="in_progress", id="7"),)),
        ts=0.0)
    t.apply_plan(AgentPlan(entries=(
        PlanEntry(content="new name", status="in_progress", id="7"),)),
        ts=5.0)
    st = t.snapshot(ts=5.0)
    assert st.tasks[0].subject == "new name"
    assert st.tasks[0].working_s == 5.0


def test_snapshot_sources_match_positionally():
    """TodoWrite and ACP have no ids; resending a full ordered list is
    itself the identity claim."""
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "in_progress"), ("b", "pending")), ts=0.0)
    t.apply_plan(plan(("a", "completed"), ("b", "in_progress")), ts=4.0)
    st = t.snapshot(ts=4.0)
    assert st.tasks[0].working_s == 4.0
    assert st.tasks[1].working_s == 0.0


def test_a_removed_task_drops_out_of_the_plan():
    t = PlanTracker()
    t.apply_plan(plan(("a", "pending"), ("b", "pending")), ts=0.0)
    t.apply_plan(plan(("a", "pending")), ts=1.0)
    assert t.snapshot(ts=1.0).total == 1


# -- roll-up ---------------------------------------------------------

def test_roll_up_carries_the_current_task():
    t = PlanTracker()
    t.set_working(True, ts=0.0)
    t.apply_plan(plan(("a", "completed"), ("b", "in_progress")), ts=0.0)
    r = t.roll_up(ts=3.0)
    assert (r.done, r.total, r.current) == (1, 2, "b")
    assert r.current_working_s == 3.0


def test_roll_up_with_no_current_task():
    t = PlanTracker()
    t.apply_plan(plan(("a", "completed")), ts=0.0)
    r = t.roll_up(ts=3.0)
    assert r.current is None and (r.done, r.total) == (1, 1)


# -- replay equivalence ----------------------------------------------

def test_replaying_the_same_event_sequence_reproduces_the_state():
    """The property that matters most. If the tracker ever reads a real
    clock instead of the ts it is handed, this is what fails."""
    script = [
        ("working", True, 0.0),
        ("plan", plan(("a", "in_progress"), ("b", "pending")), 0.0),
        ("working", False, 12.0),
        ("working", True, 900.0),                       # long idle gap
        ("plan", plan(("a", "completed"), ("b", "in_progress")), 903.0),
        ("working", False, 910.0),
    ]

    def fold():
        t = PlanTracker()
        for kind, value, ts in script:
            if kind == "working":
                t.set_working(value, ts=ts)
            else:
                t.apply_plan(value, ts=ts)
        return t.snapshot(ts=2000.0)

    live, replayed = fold(), fold()
    assert live == replayed
    assert live.tasks[0].working_s == 15.0    # 12 + 3, idle gap excluded
    assert live.tasks[1].working_s == 7.0


# -- AgentSession wiring ---------------------------------------------

class _FakeSession:
    """Mirrors tests/test_core_session.py's FakeSession."""

    def __init__(self, events=()):
        self._events = list(events)
        self.started = False
        self.closed = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


@pytest.fixture
def session():
    return AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="h-plan")


def _entries(*pairs):
    return tuple(PlanEntry(content=c, status=s) for c, s in pairs)


def test_agent_session_folds_plans_into_its_tracker(session):
    session._fire_event(AgentPlan(entries=_entries(
        ("a", "completed"), ("b", "in_progress"))))
    st = session.plan_state()
    assert (st.done, st.total) == (1, 2)
    assert session.plan_roll_up().current == "b"


def test_a_subagent_plan_does_not_overwrite_the_top_level_plan(session):
    """The failure this guards: a 1-item subagent plan replacing the
    parent's 3-item one, so the strip reads 0/1 while the real plan is
    1/3."""
    session._fire_event(AgentPlan(entries=_entries(
        ("a", "completed"), ("b", "in_progress"), ("c", "pending"))))
    session._fire_event(AgentPlan(
        entries=_entries(("sub", "in_progress")),
        parent_tool_use_id="tool_1"))

    assert session.plan_state().total == 3          # unchanged
    assert session.subplan_states()["tool_1"].total == 1


def test_two_subagents_keep_separate_plans(session):
    session._fire_event(AgentPlan(entries=_entries(("x", "in_progress")),
                                  parent_tool_use_id="tool_1"))
    session._fire_event(AgentPlan(entries=_entries(("y", "pending"),
                                                   ("z", "pending")),
                                  parent_tool_use_id="tool_2"))
    subs = session.subplan_states()
    assert subs["tool_1"].total == 1 and subs["tool_2"].total == 2


def test_turn_state_drives_the_working_flag(session):
    session._emit_state(AgentState.working, finished=False)
    assert session.plan.working is True
    session._emit_state(AgentState.ready, finished=True)
    assert session.plan.working is False


def test_subagent_trackers_follow_turn_state_too(session):
    session._emit_state(AgentState.working, finished=False)
    session._fire_event(AgentPlan(entries=_entries(("x", "in_progress")),
                                  parent_tool_use_id="tool_1"))
    assert session.subplans["tool_1"].working is True
    session._emit_state(AgentState.ready, finished=True)
    assert session.subplans["tool_1"].working is False


def test_a_session_with_no_plan_rolls_up_empty(session):
    r = session.plan_roll_up()
    assert (r.done, r.total, r.current) == (0, 0, None)
