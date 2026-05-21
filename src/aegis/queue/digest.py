"""QueueDigest — push-based aggregator over QueueManager events.

Maintains in-memory per-queue counters plus a windowed task list.
The strip and the dashboard both read snapshots from this single
source of truth so they never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueEvent,
    QueueStarted,
)

RECENT_CAP = 10


@dataclass(frozen=True)
class QueueView:
    name: str
    agent: str
    max_parallel: int
    running: int
    queued: int
    ok: int
    err: int


@dataclass(frozen=True)
class TaskView:
    task_id: str
    queue: str
    state: str   # "queued" | "running" | "ok" | "err"
    payload_summary: str
    worker_handle: str | None
    agent_slug: str | None
    from_sender: str
    enqueued_at: str | None
    dispatched_at: str | None
    started_at: str | None
    completed_at: str | None
    result: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Snapshot:
    queues: list[QueueView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)
    last_started: TaskView | None = None


def _payload_summary(payload: str, limit: int = 64) -> str:
    first = next((ln for ln in payload.splitlines() if ln.strip()), "")
    return (first if len(first) <= limit
            else first[: limit - 1].rstrip() + "…")


class QueueDigest:
    def __init__(self, manager) -> None:
        self._manager = manager
        self._unsub = None
        self._tasks: dict[str, TaskView] = {}
        # ordered task ids — insertion order = enqueue order
        self._order: list[str] = []
        self._counters: dict[str, dict[str, int]] = {}
        # cache of queue config (immutable for the session)
        self._queues: list[QueueView] = []
        # last task that transitioned to running
        self._last_started: TaskView | None = None

    def start(self) -> None:
        # Hydrate queue config from manager
        cfg = getattr(self._manager, "_queues", {})
        for name in sorted(cfg):
            q = cfg[name]
            self._counters[name] = {"ok": 0, "err": 0}
            self._queues.append(QueueView(
                name=q.name, agent=q.agent_profile,
                max_parallel=q.max_parallel,
                running=0, queued=0, ok=0, err=0))
        self._unsub = self._manager.subscribe(self._on_event)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def snapshot(self) -> Snapshot:
        # Recompute per-queue live counts from task state
        running = {q.name: 0 for q in self._queues}
        queued = {q.name: 0 for q in self._queues}
        for t in self._tasks.values():
            if t.queue not in running:
                continue
            if t.state == "running":
                running[t.queue] += 1
            elif t.state == "queued":
                queued[t.queue] += 1
        queues = [
            replace(
                q, running=running[q.name], queued=queued[q.name],
                ok=self._counters[q.name]["ok"],
                err=self._counters[q.name]["err"])
            for q in self._queues
        ]
        # Visible task list: queued + running first (any order), then
        # completed in reverse time, capped at RECENT_CAP.
        active = [self._tasks[tid] for tid in self._order
                  if self._tasks[tid].state in ("queued", "running")]
        finished = [self._tasks[tid] for tid in self._order
                    if self._tasks[tid].state in ("ok", "err")]
        finished_recent = list(reversed(finished))[:RECENT_CAP]
        return Snapshot(
            queues=queues,
            tasks=finished_recent + active,
            last_started=self._last_started,
        )

    def _on_event(self, ev: QueueEvent) -> None:
        if isinstance(ev, QueueEnqueued):
            self._tasks[ev.task_id] = TaskView(
                task_id=ev.task_id, queue=ev.queue,
                state="queued",
                payload_summary=_payload_summary(ev.payload),
                worker_handle=None, agent_slug=None,
                from_sender=ev.enqueued_by,
                enqueued_at=None,
                dispatched_at=None, started_at=None,
                completed_at=None)
            self._order.append(ev.task_id)
        elif isinstance(ev, QueueDispatched):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            self._tasks[ev.task_id] = replace(
                t, worker_handle=ev.worker_handle,
                agent_slug=ev.agent_slug)
        elif isinstance(ev, QueueStarted):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            running = replace(t, state="running")
            self._tasks[ev.task_id] = running
            self._last_started = running
        elif isinstance(ev, QueueCompleted):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            state = "ok" if ev.outcome == "completed" else "err"
            self._tasks[ev.task_id] = replace(
                t, state=state, completed_at=ev.completed_at,
                result=ev.result, error=ev.error)
            bucket = "ok" if state == "ok" else "err"
            if t.queue in self._counters:
                self._counters[t.queue][bucket] += 1
