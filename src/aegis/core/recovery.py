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
