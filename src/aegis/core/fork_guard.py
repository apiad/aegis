"""The gate on branching a conversation.

A fork loads an existing conversation and continues it under a new
handle. That only works if there is something coherent to load, so
``fork`` refuses on three conditions — and, like ``close_guard``,
reports all of them at once rather than one per attempt.

The mid-turn refusal is the one that cost something to learn. Probed
2026-07-31: ``claude`` appends each message as it produces it, so a live
session's tail is an ``assistant`` ``tool_use`` with no matching
``tool_result``. A fork inherits that dangling call and burns four turns
failing to reconcile it — 42.7s and $1.38 for no answer. Branching from
"the last completed turn" is not something the harness offers; a
mid-turn fork is torn, not merely stale.

The policy is a pure function over gathered facts — the caller does the
gathering, this decides.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForkFacts:
    exists: bool
    session_id: str | None         # driver-side conversation id, once known
    supports_fork: bool            # the target's driver capability
    state: str                     # "ready" | "working" | "error"
    driver: str                    # harness slug, for the refusal message


def refuse_reasons(facts: ForkFacts, *, target: str) -> list[str]:
    """Why ``target`` may not be forked. Empty list means it may."""
    if not facts.exists:
        return [f"no session {target!r}"]

    reasons: list[str] = []
    if facts.session_id is None:
        reasons.append(
            f"{target!r} has no session id yet (nothing to fork from — "
            "it has not produced its first SystemInit)")
    if not facts.supports_fork:
        reasons.append(
            f"driver {facts.driver!r} does not support session fork")
    if facts.state == "working":
        reasons.append(
            f"{target!r} is mid-turn (a fork would branch from a dangling "
            "tool call — wait for the turn to finish)")
    return reasons
