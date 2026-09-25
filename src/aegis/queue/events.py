"""QueueEvent taxonomy + observer types.

Push-based observability surface for the queue substrate. Every
QueueManager state transition emits exactly one event after its
JSONL log entry is committed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class QueueEnqueued:
    task_id: str
    queue: str
    payload: str
    enqueued_by: str


@dataclass(frozen=True)
class QueueDispatched:
    task_id: str
    queue: str
    worker_handle: str
    agent_slug: str


@dataclass(frozen=True)
class QueueStarted:
    task_id: str
    queue: str


@dataclass(frozen=True)
class QueueCompleted:
    task_id: str
    queue: str
    # "recoverable" is a PARKED worker, not a failure: its session is alive
    # with the conversation intact and `aegis_task_resume` can put it back to
    # work. A consumer that folds it into "failed" tells its caller the work
    # is lost when it is one call from continuing.
    outcome: Literal["completed", "failed", "interrupted", "recoverable"]
    result: str | None
    error: str | None
    completed_at: str


QueueEvent = Union[
    QueueEnqueued,
    QueueDispatched,
    QueueStarted,
    QueueCompleted,
]

QueueObserver = Callable[[QueueEvent], None]
Unsubscribe = Callable[[], None]
