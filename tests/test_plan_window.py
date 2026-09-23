"""Which plan tasks the sidebar shows.

A plan is unbounded and the column is not: twenty tasks is twenty rows,
which evicts QUEUES, MONITORS, REPOS and SYSTEM — the four sections
ordered below PLAN precisely because they are stable, not because they
are unimportant.
"""
from aegis.plan.models import PlanState, PlanTask
from aegis.plan.window import window


def _plan(n, current=None):
    return PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=f"task {i}",
                 status="in_progress" if i == current
                 else "completed" if current is not None and i < current
                 else "pending")
        for i in range(n)))


def test_a_short_plan_is_shown_whole():
    w, hidden = window(_plan(3, current=1))
    assert len(w.tasks) == 3 and hidden == 0


def test_a_long_plan_keeps_the_current_task_with_a_neighbour_each_side():
    w, hidden = window(_plan(20, current=10))
    assert [t.key for t in w.tasks] == ["9", "10", "11", "12"]
    assert hidden == 16
    assert w.tasks[1].status == "in_progress"


def test_a_plan_with_nothing_in_progress_shows_the_head_of_the_queue():
    """All pending — nothing to centre on. The window must not raise and
    must not come back empty."""
    w, hidden = window(_plan(20))
    assert [t.key for t in w.tasks] == ["0", "1", "2"]
    assert hidden == 17


def test_a_finished_plan_shows_its_tail():
    """Every task completed. Centring on `current` is impossible; the
    interesting end is the one that just finished."""
    s = PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=f"t{i}", status="completed")
        for i in range(20)))
    w, hidden = window(s)
    assert [t.key for t in w.tasks] == ["17", "18", "19"]
    assert hidden == 17


def test_an_empty_plan_windows_to_nothing():
    w, hidden = window(PlanState())
    assert w.tasks == () and hidden == 0


def test_the_window_preserves_the_whole_plans_counts():
    """`done` and `total` on the windowed state would lie. The caller reads
    them off the original, and this pins that the window does not pretend
    to be the plan."""
    full = _plan(20, current=10)
    w, _ = window(full)
    assert full.total == 20 and w.total == 4


def test_a_half_done_plan_between_tasks_shows_what_is_outstanding():
    """Reachable at every task boundary: the harness marks one completed
    before it marks the next in_progress, so `current` is momentarily None
    on a plan that is well under way. Taking the head then showed three
    tasks finished an hour ago and hid the only outstanding one, in the
    section whose whole purpose is what is happening now."""
    tasks = tuple(
        PlanTask(key=str(i), subject=f"task {i}",
                 status="completed" if i < 19 else "pending")
        for i in range(20))
    w, hidden = window(PlanState(tasks=tasks))
    assert any(t.status == "pending" for t in w.tasks), [t.key for t in w.tasks]
    assert w.tasks[-1].key == "19"


def test_a_plan_stopped_midway_shows_the_boundary_not_the_beginning():
    tasks = tuple(
        PlanTask(key=str(i), subject=f"task {i}",
                 status="completed" if i < 10 else "pending")
        for i in range(20))
    w, _ = window(PlanState(tasks=tasks))
    assert [t.key for t in w.tasks] == ["9", "10", "11"]
