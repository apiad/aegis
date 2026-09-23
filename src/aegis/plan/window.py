"""Which plan tasks a bounded column shows.

Pure, and separate from ``render_plan_dock`` on purpose: that renderer has
its own contract (its header, its ``(no plan)`` body) asserted in
``tests/test_plan_render.py``, and choosing which tasks to show is a
different question from how a task row looks. Two functions, one job each.
"""

from __future__ import annotations

from aegis.plan.models import PlanState

# One completed task above the current one and two pending below. The task
# above is what you just finished, which is the cheapest orientation there
# is; two below is how much of what comes next fits without the section
# starting to evict its neighbours.
WINDOW_BEFORE = 1
WINDOW_AFTER = 2


def window(
    state: PlanState,
    before: int = WINDOW_BEFORE,
    after: int = WINDOW_AFTER,
) -> tuple[PlanState, int]:
    """``(windowed, hidden)`` — the tasks to draw, and how many were left out.

    Centred on the in-progress task. With none in progress there is nothing
    to centre on, and the two cases differ: a plan not yet started is asking
    what comes first, and a finished one is asking what just happened, so the
    window takes the head in the first case and the tail in the second.

    The returned state's ``done`` and ``total`` describe the *window*, not
    the plan. The caller reads the real counts off the original.
    """
    tasks = state.tasks
    span = before + after + 1
    if len(tasks) <= span:
        return state, 0

    current = next((i for i, t in enumerate(tasks) if t.status == "in_progress"), None)
    if current is not None:
        lo = current - before
        n = span
    else:
        # One row shorter with no current task: the extra row exists to mark
        # where you are, and there is nowhere to mark.
        n = span - 1
        # Anchored on the boundary between what is finished and what is
        # not, never on "is anything unfinished". Keying off the latter
        # took the HEAD of any plan with a single incomplete task — so a
        # 20-task plan with 19 done showed three tasks finished an hour ago
        # and hid the only outstanding one. That state is reachable at
        # every task boundary, because the harness marks one completed
        # before it marks the next in progress.
        first_open = next(
            (i for i, t in enumerate(tasks) if t.status != "completed"), None
        )
        # Nothing open: the plan is finished and the interesting end is the
        # one that just closed.
        lo = len(tasks) - n if first_open is None else first_open - before

    # Slid back rather than truncated when the window runs off the end: the
    # window exists to spend a fixed number of rows, and a plan that got
    # SHORTER on screen as it neared completion was leaving them unused.
    lo = max(0, min(lo, len(tasks) - n))
    hi = lo + n
    return PlanState(tasks=tasks[lo:hi]), len(tasks) - (hi - lo)
