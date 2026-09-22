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
        lo = max(0, current - before)
        hi = min(len(tasks), current + after + 1)
    else:
        # One row shorter with no current task: the extra row exists to mark
        # where you are, and there is nowhere to mark.
        n = span - 1
        # Not started asks what comes first; finished asks what just
        # happened. Head in the first case, tail in the second.
        lo = 0 if any(t.status != "completed" for t in tasks) else len(tasks) - n
        lo = max(0, lo)
        hi = min(len(tasks), lo + n)

    return PlanState(tasks=tasks[lo:hi]), len(tasks) - (hi - lo)
