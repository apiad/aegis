"""Subscribe to QueueManager completion events and fire remote callbacks."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from aegis.queue.events import QueueCompleted, QueueEvent
from aegis.queue.manager import QueueManager
from aegis.remote.client import remote_callback
from aegis.remote.config import RemoteSpec

_log = logging.getLogger(__name__)


def install_callback_observer(
    qm: QueueManager,
    *,
    remotes: Mapping[str, RemoteSpec],
    self_peer_name: str,
) -> None:
    """Hook a completion observer that fires /remote/v1/callback per task.

    Only QueueCompleted events with a task whose `callback_to` is set are
    eligible. Best-effort POST, no retry. `self_peer_name` is what we
    identify ourselves as in the callback body's `from_peer` field.
    """
    # Hold strong references to in-flight callback tasks. Without this, Python
    # may garbage-collect the Task object before its coroutine completes; see
    # https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
    # ("Save a reference to the result of this function, to avoid a task
    #  disappearing mid-execution.").
    _inflight: set[asyncio.Task] = set()

    def _observer(ev: QueueEvent) -> None:
        if not isinstance(ev, QueueCompleted):
            return
        task = qm._all.get(ev.task_id)
        if task is None or not task.callback_to:
            return
        spec = remotes.get(task.callback_to)
        if spec is None:
            qm._log(task.queue, {
                "event": "callback_dropped",
                "task_id": task.id,
                "reason": "unknown_peer",
                "callback_to": task.callback_to,
            })
            return
        status_wire = {"completed": "ok",
                       "failed": "failed",
                       "interrupted": "interrupted"}[ev.outcome]
        # Task dataclass has `result` (for completed) and `error` (for failed),
        # NOT result_text. Use whichever is populated.
        result_text = ev.result if ev.outcome == "completed" else (ev.error or "")
        body = {
            "task_id":     task.id,
            "queue":       task.queue,
            "from_peer":   self_peer_name,
            "to_handle":   task.callback_handle,
            "status":      status_wire,
            "result_text": result_text or "",
            # Task has no `started_at` field; use enqueued_at as the closest
            # analog for now (this is the spec's intent — when the task
            # entered the system).
            "started_at":  task.enqueued_at or "",
            "ended_at":    ev.completed_at or "",
        }
        # Fire-and-forget — observer must not block QueueManager.
        t = asyncio.create_task(_fire(qm, task.queue, task.id, spec, body))
        _inflight.add(t)
        t.add_done_callback(_inflight.discard)

    qm.subscribe(_observer)


async def _fire(qm: QueueManager, queue: str, task_id: str,
                spec: RemoteSpec, body: dict) -> None:
    result = await remote_callback(spec, body)
    qm._log(queue, {
        "event": "callback_attempted",
        "task_id": task_id,
        "outcome": "delivered" if result.get("ok") else result.get("error"),
    })
