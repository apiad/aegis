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
