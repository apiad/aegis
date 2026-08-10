"""The gate on one agent closing another.

Closing a session ends a live conversation and drops whatever it still
had in flight, so ``aegis_close`` refuses unless two things hold: the
caller spawned the target, and the target is demonstrably finished.

The policy is a pure function over gathered facts — the MCP tool does
the gathering, this decides. Every failing condition is reported, not
just the first: an agent that has to call again for each new reason
learns nothing about how long to wait.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CloseFacts:
    exists: bool
    spawned_by: str | None
    state: str                     # "ready" | "working" | "error"
    monitors: int                  # live monitors it armed
    reminders: int                 # pending future-time reminders
    inbox_depth: int               # messages delivered but not yet consumed
    worker_label: str | None       # "<queue>#<id>" while running a queue task
    loop_armed: bool
    claims: int                    # file claims it holds


def refuse_reasons(facts: CloseFacts, *,
                   requester: str, target: str) -> list[str]:
    """Why ``requester`` may not close ``target``. Empty list means it may."""
    if not facts.exists:
        return [f"no session {target!r}"]
    if requester == target:
        return ["an agent cannot close itself"]
    if facts.spawned_by != requester:
        origin = (f"it was spawned by {facts.spawned_by!r}"
                  if facts.spawned_by else "it was not spawned by an agent")
        return [f"{requester!r} did not spawn {target!r} ({origin})"]

    reasons: list[str] = []
    if facts.state == "working":
        reasons.append(f"{target!r} is mid-turn")
    if facts.monitors:
        reasons.append(f"{facts.monitors} live monitor(s) would be orphaned")
    if facts.reminders:
        reasons.append(
            f"{facts.reminders} pending reminder(s) would never fire")
    if facts.inbox_depth:
        reasons.append(
            f"{facts.inbox_depth} undelivered inbox message(s) would be lost")
    if facts.worker_label:
        reasons.append(f"still running queue task {facts.worker_label}")
    if facts.loop_armed:
        reasons.append("a loop is armed (stop it first)")
    if facts.claims:
        reasons.append(f"holds {facts.claims} file claim(s)")
    return reasons


def still_working_reasons(facts: CloseFacts) -> list[str]:
    """Why this session is not done, even though its turn just ended.

    The *substrate's* half of the question above, with the ownership half
    dropped: nobody is asking permission here, so there is no requester
    and no ``spawned_by``. Ending a turn is how an agent WAITS — the
    monitor briefing says so in as many words ("returns {monitor_id}
    immediately; END YOUR TURN") — so a turn boundary on its own says
    nothing about whether the work is finished.

    Two of ``refuse_reasons``'s conditions are deliberately absent.

    **``worker_label``** — every queue worker carries one by construction,
    so including it would defer every worker forever.

    **``claims``** — the one condition that is not self-terminating. A
    monitor has a timeout, a reminder has a fire time, an inbox message
    resolves at the next turn boundary; each of those *will* wake the
    session, so deferring on them is bounded. A file claim is released
    only by the agent that took it, so a worker that forgot would pin a
    ``max_parallel`` slot until the process dies. ``aegis_close`` is right
    to refuse on one — a human is asking and can go look — and the
    substrate is right not to. Claims auto-reap on close regardless.
    """
    reasons: list[str] = []
    if facts.state == "working":
        reasons.append("mid-turn")
    if facts.monitors:
        reasons.append(f"{facts.monitors} live monitor(s) still watching")
    if facts.reminders:
        reasons.append(f"{facts.reminders} reminder(s) yet to fire")
    if facts.inbox_depth:
        reasons.append(f"{facts.inbox_depth} inbox message(s) not consumed")
    if facts.loop_armed:
        reasons.append("a loop is armed")
    return reasons


def gather_facts(bridge, handle: str, *, state: str | None = None
                 ) -> CloseFacts:
    """Read the coordination planes off ``bridge`` for one handle.

    Every plane is optional and every read is defended: a frontend that
    never wired one up must degrade to "nothing pending there", never to
    a block or a crash. That asymmetry is deliberate in ``aegis_close``
    (a missing plane is not a reason to refuse) and it is deliberate here
    (a missing plane is not a reason to keep a worker alive forever).

    ``state`` overrides the session-list lookup for callers that already
    know it — the queue's finalizer is handed the terminal state directly,
    and by the time it looks the roster may already disagree.
    """
    # The roster is a plane like any other, and is defended like one: a
    # bridge without `list_sessions` must degrade to "nothing known about
    # it", not take the caller down. Leaving this one bare is how the
    # queue's first cut of `_still_working` silently returned "not
    # waiting" for every worker — an AttributeError here, swallowed
    # there, and the fix looked like it had changed nothing.
    try:
        info = next((s for s in bridge.list_sessions()
                     if s.handle == handle), None)
    except Exception:  # noqa: BLE001
        info = None

    def _count(fn, *a, **kw) -> int:
        try:
            return len(fn(*a, **kw) or [])
        except Exception:  # noqa: BLE001 — a missing plane is not a block
            return 0

    mm = getattr(bridge, "monitor_manager", None)
    rs = getattr(bridge, "reminder_service", None)
    ib = getattr(bridge, "inbox_router", None)
    qm = getattr(bridge, "queue_manager", None)
    ls = getattr(bridge, "loop_service", None)
    locks = getattr(bridge, "locks", None)

    loop_armed = False
    if ls is not None:
        try:
            loop_armed = bool(ls.status(from_handle=handle).get("loop"))
        except Exception:  # noqa: BLE001
            loop_armed = False
    worker_label = None
    if qm is not None:
        try:
            worker_label = qm.worker_label(handle)
        except Exception:  # noqa: BLE001
            worker_label = None
    claims = 0
    if locks is not None:
        try:
            claims = len([c for c in (locks.active() or [])
                          if getattr(c, "handle", None) == handle])
        except Exception:  # noqa: BLE001
            claims = 0

    return CloseFacts(
        exists=info is not None,
        spawned_by=getattr(info, "spawned_by", None),
        state=state if state is not None else getattr(info, "state", "ready"),
        monitors=(_count(mm.snapshot, for_handle=handle)
                  if mm is not None else 0),
        reminders=(_count(rs.list_reminders, from_handle=handle)
                   if rs is not None else 0),
        inbox_depth=(_count(ib.pending, handle) if ib is not None else 0),
        worker_label=worker_label,
        loop_armed=loop_armed,
        claims=claims,
    )
