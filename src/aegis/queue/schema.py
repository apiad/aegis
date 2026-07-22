"""Queue/Task/InboxMessage records + sender-tag helpers + ULID + ISO timestamp.

The substrate's universal-tagging principle: every inbox message and every
queue entry carries a typed `sender` prefix (queue:<name>, agent:<handle>,
telegram, system, plus reserved prefixes) and an ISO-8601 timestamp.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aegis.budget.budgets import Budget

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def sender_queue(name: str) -> str:
    return f"queue:{name}"


def sender_agent(handle: str) -> str:
    return f"agent:{handle}"


def sender_monitor(name: str) -> str:
    """Sender tag for an ``aegis_monitor`` completion/failure callback."""
    return f"monitor:{name}"


def sender_reminder() -> str:
    """Sender tag for a self-left reminder delivered back to the agent's own
    inbox — either at turn-end (the lowest-priority turn boundary tier) or at
    a scheduled future time."""
    return "reminder"


def sender_user() -> str:
    """Sender tag for a message Alex typed into the text box. Renders
    headerless (a plain user turn), unlike agent/queue/telegram inbox
    messages which carry a ``> from …`` substrate header."""
    return "user"


def new_ulid() -> str:
    """26-char Crockford-base32 ULID: 48-bit ms timestamp + 80-bit randomness.

    Lexicographic sort = chronological sort (timestamp is the high-order
    portion).
    """
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = secrets.randbits(80)
    n = (ts << 80) | rnd
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


def now_iso() -> str:
    """UTC ISO-8601 with second precision: 2026-05-20T07:14:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Queue:
    name: str
    agent_profile: str
    max_parallel: int
    provider: str = ""   # populated from agent_profile at config-load
    model: str = ""      # populated from agent_profile at config-load
    budgets: list[Budget] = field(default_factory=list)


@dataclass(frozen=True)
class Task:
    id: str
    queue: str
    payload: str
    enqueued_by: str       # SenderTag
    enqueued_at: str       # iso8601
    callback: bool
    status: str            # "pending" | "dispatched" | "completed"
                           #   | "failed" | "cancelled"
    worker_handle: str | None = None
    result: str | None = None
    error: str | None = None
    completed_at: str | None = None
    callback_to: str | None = None
    callback_handle: str | None = None


@dataclass(frozen=True)
class Delivery:
    """Receipt returned by ``deliver``: did the message ``landed`` (consumed
    immediately into a turn) or ``queued`` (buffered behind an in-flight
    turn)? ``depth`` is the 1-based queue position when queued, 0 when
    landed."""
    disposition: str   # "landed" | "queued"
    depth: int         # queue position; 0 when landed


@dataclass(frozen=True)
class InboxMessage:
    sender: str
    timestamp: str
    body: str
    task_id: str | None = None
    status: str | None = None   # "ok" | "error" | None


def render_inbox_header(msg: InboxMessage) -> str:
    """One-line substrate header prefixed to every delivered inbox message.

    User text-box messages (``sender == "user"``) render headerless — they
    reach the agent as a plain user turn, indistinguishable from today's
    direct ``send``.
    """
    if msg.sender == "user":
        return ""
    if msg.task_id is not None:
        status = msg.status or "?"
        return (
            f"> from {msg.sender} · task#{msg.task_id} · "
            f"{status} · {msg.timestamp}"
        )
    return f"> from {msg.sender} · {msg.timestamp}"
