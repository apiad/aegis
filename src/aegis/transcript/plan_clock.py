"""How long a plan has taken: work and idle time, from the store's timestamps.

The fold calls these only where the clock changes: a plan record, a turn
opening, a turn closing. Between them the clock runs, and the browser adds
``now - at`` to whatever is running, so the standing, and the meta that
persists it, do not change on every line of a turn (spec
2026-10-09-plan-timing-design.md).

``clock`` is ``{"work_s", "idle_s", "at", "running"}``: the totals accrued up
to ``at``, and what has run since (``"work"`` or ``"idle"``). ``None`` means
no plan yet, and no time is kept. Every function returns the objects it was
given when nothing changed, because the fold compares standing by identity.
"""


def accrue(
    plan: list[dict], clock: dict | None, ts: float
) -> tuple[list[dict], dict | None]:
    """Add the time from ``clock["at"]`` to ``ts`` to whatever is running."""
    if clock is None or ts <= clock["at"]:
        return plan, clock
    dt = ts - clock["at"]
    if clock["running"] == "idle":
        return plan, {**clock, "idle_s": round(clock["idle_s"] + dt, 1), "at": ts}
    plan = [
        {**i, "work_s": round(i.get("work_s", 0.0) + dt, 1)}
        if i["state"] == "doing"
        else i
        for i in plan
    ]
    return plan, {**clock, "work_s": round(clock["work_s"] + dt, 1), "at": ts}


def switch(
    plan: list[dict], clock: dict | None, ts: float, running: str
) -> tuple[list[dict], dict | None]:
    """Accrue up to ``ts``, then run ``running`` from there."""
    if clock is None or clock["running"] == running:
        return plan, clock
    plan, clock = accrue(plan, clock, ts)
    assert clock is not None
    return plan, {**clock, "running": running}


def replan(
    old: list[dict], clock: dict | None, new: list[dict], ts: float, running: str
) -> tuple[list[dict], dict]:
    """A plan record. Items keep their time by text; a plan that shares no
    text with the last one is a new plan, and its clock starts at zero.
    ``running`` is what a new clock runs; an existing clock keeps its own."""
    if clock is not None and [(i["text"], i["state"]) for i in old] == [
        (i["text"], i["state"]) for i in new
    ]:
        return old, clock
    old, clock = accrue(old, clock, ts)
    times = {i["text"]: i.get("work_s", 0.0) for i in old}
    if clock is None or not any(i["text"] in times for i in new):
        clock = {"work_s": 0.0, "idle_s": 0.0, "at": ts, "running": running}
        times = {}
    items = [
        {"text": i["text"], "state": i["state"], "work_s": times.get(i["text"], 0.0)}
        for i in new
    ]
    return items, clock
