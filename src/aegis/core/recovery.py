"""Recovering an ephemeral agent whose turn ended badly.

An ephemeral agent (`fleet.models.EPHEMERAL_KINDS`: queue, workflow,
group) used to be closed the moment a turn ended in anything but
`ready`, and closing is irreversible — the session leaves the roster,
its MCP token is revoked, its pane is dropped, and its conversation stops
being reachable by any path aegis offers. A dropped SSH link to an
execution host arrives here as `Result(is_error=True)`, so a tunnel blip
destroyed an hour of context.

This module holds the three things every caller needs and none of the
state: the retry budget, the record that makes a conversation
rebuildable, and the rebuild itself. `QueueManager` is the first caller;
`WorkflowEngine` and the group runtime are meant to be the next, which is
why nothing here knows what a task is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aegis.tui.state import AgentState


@dataclass(frozen=True)
class Resumable:
    """Everything needed to rebuild a worker's conversation.

    `session_id` is the harness's own conversation id, latched by the
    driver on its first SystemInit. Without it there is nothing to
    resume, which is why it is recorded the moment it appears rather
    than at the end of a turn: a worker whose harness dies before any
    turn boundary is exactly the case the record exists for.
    """

    session_id: str
    agent_profile: str
    provider: str
    cwd: str
    host: str


class Outcome(StrEnum):
    done = "done"
    transient = "transient"
    terminal = "terminal"


def classify(
    state: AgentState,
    *,
    attempts: int,
    max_attempts: int,
    cancelled: bool = False,
    over_budget: bool = False,
) -> Outcome:
    """What a turn ending means for the task behind it.

    `attempts` is the number of bad turn ends this task's worker has had
    INCLUDING the one being classified, so a caller increments its
    counter BEFORE calling: `max_attempts=1` means one try and no retry.

    `transient` is the DEFAULT arm, deliberately. The tempting version
    enumerates the recoverable reasons — link_lost, a harness exception,
    a stream with no Result, a rate limit — and calls the rest terminal.
    That list needs a cross-harness vocabulary for failure that does not
    exist, and being wrong about it fails closed: it goes terminal where
    it should have retried, which is indistinguishable from the bug this
    plane exists to fix. The budget does the limiting instead, and the
    diagnostic fields go in the log to be read rather than branched on.
    """
    if state is AgentState.ready:
        return Outcome.done
    if cancelled or over_budget:
        return Outcome.terminal
    if attempts >= max_attempts:
        return Outcome.terminal
    return Outcome.transient


def resumable_from(session) -> Resumable | None:
    """The rebuild record for a live session, or None when the harness has
    not reported a conversation id yet.

    Read defensively: this runs against real AgentSessions and against
    every test double that stands in for one, and a probe that raises
    here would strand the task it was trying to save. `session_id` is a
    property that delegates to the driver, so `getattr` covers both a
    driver that reports None and a stand-in with no such attribute.
    """
    sid = getattr(session, "session_id", None)
    if not sid:
        return None
    place = getattr(session, "place", None)
    agent = getattr(session, "agent", None)
    return Resumable(
        session_id=sid,
        agent_profile=getattr(session, "agent_slug", "") or "",
        provider=getattr(agent, "harness", "") or "",
        cwd=getattr(place, "cwd", "") or "",
        host=getattr(place, "host", "") or "local",
    )


#: What a rebuilt worker is told. It has no memory of the interruption —
#: from inside, the turn simply never ended — so the message has to say
#: that its conversation survived, or it re-derives what it already knows.
NUDGE_STALL = (
    "Your aegis session was interrupted mid-task ({reason}). Your "
    "conversation is intact and you are still working the same task. "
    "Continue from where you were; do not start over."
)
NUDGE_RESTART = (
    "aegis restarted while you were mid-task. Your conversation is "
    "intact and the task is still yours. Continue from where you were; "
    "do not start over. Re-arm any monitor you were waiting on, because "
    "the process that held it is gone."
)
NUDGE_OPERATOR = (
    "The operator has put you back to work on the task you were parked "
    "on. Your conversation is intact. Continue from where you were; if "
    "you are not sure what state you left things in, check the working "
    "tree before you act."
)


async def rebuild(sm, handle: str, *, nudge: str) -> bool:
    """Replace the dead harness under `handle`, then tell it what happened.

    Returns False rather than raising when the rebuild is impossible: a
    driver whose loadSession probe fails at runtime, a session with no
    conversation id, a handle that is already gone. The caller is a
    finalizer, and a raise there strands the task it was trying to save.

    `AgentSession.adopt`, which `reconnect` calls, keeps the handle,
    log_id, inbox binding, metrics, observers and transcript. Observers
    surviving is what matters here: the queue's on_event and on_state stay
    attached across the rebuild, so there is no window in which a worker
    runs unobserved and nothing to re-wire. Spawning onto the handle
    instead would race the TUI's async pane drop and mount a second
    `#pane-<handle>`, which is DuplicateIds and the whole app.
    """
    # Imported here, not at module level: `aegis.queue.schema` imports
    # `Resumable` from this module, so a top-level import back would close
    # the cycle.
    from aegis.queue.schema import InboxMessage, now_iso, sender_substrate

    try:
        # `reconnect(allow_local=True)` has NO precondition that the
        # session is dead — it closes whatever harness is on the handle.
        # Safe here only because every call site fires after a turn has
        # already ended badly. Do not copy this into a live-session path.
        await sm.reconnect(handle, allow_local=True)
    except Exception:  # noqa: BLE001 — a failed rebuild parks, never raises
        return False
    s = sm.get(handle)
    if s is None:
        return False
    try:
        await s.deliver(
            InboxMessage(
                sender=sender_substrate(),
                timestamp=now_iso(),
                body=nudge,
            )
        )
    except Exception:  # noqa: BLE001
        return False
    return True
