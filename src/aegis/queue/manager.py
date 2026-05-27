"""QueueManager — substrate-deterministic dispatch.

One owner per `aegis serve` (or interactive) process. Pure FIFO per queue +
max-parallel cap + dispatch-on-event. No background loop: dispatch is
checked synchronously on every enqueue and on every worker completion.
Persistence + restart replay land in VS2; this build is memory-only.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from aegis.budget.cost import compute as _compute_cost
from aegis.budget.evaluator import evaluate_budgets
from aegis.budget.prices import UnknownPriceError
from aegis.events import AssistantText
from aegis.queue.events import (
    QueueCompleted,
    QueueDispatched,
    QueueEnqueued,
    QueueEvent,
    QueueObserver,
    QueueStarted,
    Unsubscribe,
)
from aegis.queue.jsonl import append_record
from aegis.queue.schema import (
    InboxMessage,
    Queue,
    Task,
    new_ulid,
    now_iso,
    sender_queue,
)
from aegis.tui.names import generate_name
from aegis.tui.state import AgentState


def _adapt_metrics(metrics):
    """Map SessionMetrics committed counters to cost.compute's expected
    attribute names. Returns a lightweight object — duck-typed."""
    class _M:
        input_tokens     = int(getattr(metrics, "c_in", 0) or 0)
        output_tokens    = int(getattr(metrics, "c_out", 0) or 0)
        cache_hit_tokens = int(getattr(metrics, "c_cached", 0) or 0)
        cache_write_tokens = 0
        thinking_tokens  = 0
    return _M


def _handle_of(sender_tag: str) -> str:
    """Extract the inbox handle from a SenderTag. Only ``agent:<handle>``
    has a delivery target in v1; others (telegram/system/queue:…) deliver
    to a sentinel handle equal to the sender — the router tolerates
    unbound handles and just buffers."""
    if sender_tag.startswith("agent:"):
        return sender_tag.split(":", 1)[1]
    return sender_tag


class QueueManager:
    def __init__(self, queues: dict[str, Queue], session_manager,
                 inbox_router,
                 *, state_dir: Path | None = None,
                 now: Callable[[], str] = now_iso,
                 handle_factory: Callable[[set[str]], str] | None = None) -> None:
        self._queues = dict(queues)
        self._sm = session_manager
        self._inbox = inbox_router
        self._state_dir = state_dir
        self._now = now
        self._handle_factory = handle_factory or generate_name
        # in-memory state
        self._pending: dict[str, list[Task]] = {q: [] for q in self._queues}
        self._inflight: dict[str, list[Task]] = {q: [] for q in self._queues}
        self._all: dict[str, Task] = {}
        # per-worker result accumulators: handle -> (task, last_assistant_text)
        self._workers: dict[str, tuple[Task, str]] = {}
        # lifecycle observers — see subscribe()
        self._observers: list[QueueObserver] = []
        # optional sink for live assistant-text forwarding (e.g. QueueDigest)
        self._assistant_text_hook: Callable[[str, str], None] | None = None

    def list_queues(self) -> list[str]:
        return sorted(self._queues)

    def register_queue(self, queue: Queue) -> None:
        """Add a queue to the live map. Idempotent if (name, queue) match;
        raises ValueError on name collision with a different queue."""
        existing = self._queues.get(queue.name)
        if existing is not None:
            if existing == queue:
                return
            raise ValueError(
                f"queue {queue.name!r} already registered")
        self._queues[queue.name] = queue
        self._pending[queue.name] = []
        self._inflight[queue.name] = []

    def subscribe(self, callback: QueueObserver) -> Unsubscribe:
        """Register an observer for every queue lifecycle transition.

        Callbacks fire after the JSONL record is committed (committed-state
        observability). Exceptions inside observers are caught and logged
        — a broken observer never poisons the substrate.
        """
        self._observers.append(callback)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._observers.remove(callback)
        return _unsubscribe

    def _emit(self, ev: QueueEvent) -> None:
        for cb in list(self._observers):
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception(
                    "queue observer raised on %s", type(ev).__name__)

    def _log(self, queue: str, event: dict) -> None:
        """Persist one lifecycle event to the queue's JSONL log.

        No-op when state_dir is not configured (VS1 in-memory mode).
        """
        if self._state_dir is None:
            return
        path = Path(self._state_dir) / "queues" / f"{queue}.jsonl"
        append_record(path, event)

    def _load_recent_jsonl(self, queue: str, max_age) -> list[dict]:
        """Read this queue's JSONL, return terminal records within max_age."""
        if self._state_dir is None:
            return []
        path = Path(self._state_dir) / "queues" / f"{queue}.jsonl"
        if not path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - max_age
        out: list[dict] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") not in ("completed", "failed"):
                continue
            ts_str = rec.get("completed_at", "")
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            if ts >= cutoff:
                out.append(rec)
        return out

    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool = False,
                callback_to: str | None = None,
                callback_handle: str | None = None) -> tuple[str, int] | dict:
        if queue not in self._queues:
            raise KeyError(queue)
        q = self._queues[queue]
        if q.budgets:
            tail = self._load_recent_jsonl(
                queue, max_age=max(b.window for b in q.budgets))
            decision = evaluate_budgets(
                tail, q.budgets, datetime.now(timezone.utc))
            if not decision.allowed:
                return {
                    "error": f"queue {queue!r} over budget",
                    "queue": queue,
                    "blocked_by": [
                        {"constraint": c.constraint,
                         "limit": str(c.limit),
                         "spent": str(c.spent),
                         "window": c.window_str,
                         "unblock_at": c.unblock_at.isoformat().replace(
                             "+00:00", "Z") if c.unblock_at else None}
                        for c in decision.blocked_by],
                    "unblock_at": decision.unblock_at.isoformat().replace(
                        "+00:00", "Z") if decision.unblock_at else None,
                }
        task = Task(
            id=new_ulid(), queue=queue, payload=payload,
            enqueued_by=enqueued_by, enqueued_at=self._now(),
            callback=callback, status="pending",
            callback_to=callback_to, callback_handle=callback_handle)
        self._pending[queue].append(task)
        self._all[task.id] = task
        position = len(self._pending[queue])
        self._log(queue, {
            "event": "enqueued", "task_id": task.id, "queue": queue,
            "payload": payload, "enqueued_by": enqueued_by,
            "enqueued_at": task.enqueued_at, "callback": callback})
        self._emit(QueueEnqueued(
            task_id=task.id, queue=queue,
            payload=payload, enqueued_by=enqueued_by))
        self._try_dispatch(queue)
        return task.id, position

    def status(self, task_id: str) -> dict | None:
        t = self._all.get(task_id)
        if t is None:
            return None
        return {
            "status": t.status,
            "result": t.result,
            "error": t.error,
            "completed_at": t.completed_at,
            "queued_position": self._position_of(t),
        }

    def _position_of(self, t: Task) -> int | None:
        if t.status != "pending":
            return None
        fifo = self._pending[t.queue]
        for i, x in enumerate(fifo, start=1):
            if x.id == t.id:
                return i
        return None

    def _try_dispatch(self, queue: str) -> None:
        q = self._queues[queue]
        while (len(self._inflight[queue]) < q.max_parallel
               and self._pending[queue]):
            task = self._pending[queue].pop(0)
            used = (set(self._workers)
                    | {s.handle for s in getattr(self._sm,
                                                  "_sessions", [])})
            worker_handle = self._handle_factory(used)
            dispatched = Task(**{**task.__dict__,
                                 "status": "dispatched",
                                 "worker_handle": worker_handle})
            self._all[task.id] = dispatched
            self._inflight[queue].append(dispatched)
            self._workers[worker_handle] = (dispatched, "")
            self._log(queue, {
                "event": "dispatched", "task_id": task.id,
                "worker_handle": worker_handle})
            self._emit(QueueDispatched(
                task_id=task.id, queue=queue,
                worker_handle=worker_handle,
                agent_slug=q.agent_profile))
            self._emit(QueueStarted(task_id=task.id, queue=queue))
            # Use the sync seam — async AppBridge.spawn is for workflow.
            sync_spawn = getattr(self._sm, "_sync_spawn", self._sm.spawn)
            session = sync_spawn(q.agent_profile,
                                 opening_prompt=task.payload,
                                 handle=worker_handle)
            self._attach_observers(session, dispatched)

    def _attach_observers(self, session, task: Task) -> None:
        # add_event_observer / add_state_observer (not the primary on_event /
        # on_state slots) so the substrate composes cleanly with a frontend
        # that already claimed the primary hooks for its renderer — notably
        # the TUI's ConversationPane._core, whose renderer cannot be
        # clobbered.
        def on_event(_s, ev):
            if isinstance(ev, AssistantText):
                t, _last = self._workers[session.handle]
                self._workers[session.handle] = (t, ev.text)
                if self._assistant_text_hook is not None:
                    try:
                        self._assistant_text_hook(session.handle, ev.text)
                    except Exception:  # noqa: BLE001
                        pass

        def on_state(_s, st, finished):
            if not finished:
                return
            asyncio.create_task(self._finalize(session, st))

        session.add_event_observer(on_event)
        session.add_state_observer(on_state)

    async def _finalize(self, session, st) -> None:
        if session.handle not in self._workers:
            return
        task, last_text = self._workers.pop(session.handle)
        ok = (st is AgentState.ready)
        status = "completed" if ok else "failed"
        result = last_text if ok else None
        error = None if ok else (last_text or "worker exited with error")
        completed = Task(**{**task.__dict__,
                            "status": status,
                            "result": result,
                            "error": error,
                            "completed_at": self._now()})
        self._all[task.id] = completed
        self._inflight[task.queue] = [
            t for t in self._inflight[task.queue] if t.id != task.id]
        q = self._queues[task.queue]
        try:
            metrics = getattr(session, "metrics", None)
            cost_dict = _compute_cost(
                _adapt_metrics(metrics),
                provider=q.provider, model=q.model,
            ).as_dict()
        except UnknownPriceError as e:
            cost_dict = {"error": "unknown_model", "detail": str(e)}
        except Exception as e:  # noqa: BLE001 — don't let cost break finalizer
            cost_dict = {"error": "compute_failed", "detail": str(e)}
        self._log(task.queue, {
            "event": status, "task_id": task.id,
            "result": result, "error": error,
            "completed_at": completed.completed_at,
            "cost": cost_dict})
        self._emit(QueueCompleted(
            task_id=task.id, queue=task.queue,
            outcome="completed" if ok else "failed",
            result=result, error=error,
            completed_at=completed.completed_at))
        if task.callback:
            body = result if ok else (error or "")
            msg = InboxMessage(
                sender=sender_queue(task.queue),
                timestamp=self._now(),
                body=body,
                task_id=task.id,
                status=("ok" if ok else "error"))
            await self._inbox.deliver(_handle_of(task.enqueued_by), msg)
        try:
            await self._sm.close(session.handle)
        except Exception:  # noqa: BLE001 — close is best-effort
            pass
        self._try_dispatch(task.queue)

    # ----- VS2 lifecycle hooks --------------------------------------
    async def start(self) -> None:
        """Replay persisted state on boot. Tasks that were dispatched but
        never reached completed/failed are marked ``failed:interrupted``
        and a failure callback is delivered to the producer's inbox
        (durable on disk even if no live session is bound). Pending-at-
        crash tasks are re-queued at head-of-FIFO."""
        if self._state_dir is None:
            return
        from aegis.queue.jsonl import read_records
        qdir = Path(self._state_dir) / "queues"
        if not qdir.exists():
            return
        for path in sorted(qdir.glob("*.jsonl")):
            queue_name = path.stem
            if queue_name not in self._queues:
                # Orphaned log from a removed queue — leave the file
                # untouched; reading other queues' logs is unaffected.
                continue
            # Per-task latest-aggregate view. Last event wins for status;
            # all fields merged so the final dict has enqueued metadata
            # plus dispatched/completed extras.
            tasks: dict[str, dict] = {}
            for rec in read_records(path):
                tid = rec.get("task_id")
                if tid is None:
                    continue
                tasks.setdefault(tid, {}).update(rec)
                tasks[tid]["status"] = rec["event"]
            for tid, r in tasks.items():
                if r["status"] == "dispatched":
                    await self._mark_interrupted(queue_name, tid, r)
                elif r["status"] in ("completed", "failed"):
                    self._all[tid] = Task(
                        id=tid, queue=queue_name,
                        payload=r.get("payload", ""),
                        enqueued_by=r.get("enqueued_by", "system"),
                        enqueued_at=r.get("enqueued_at", self._now()),
                        callback=bool(r.get("callback", False)),
                        status=r["status"],
                        worker_handle=r.get("worker_handle"),
                        result=r.get("result"),
                        error=r.get("error"),
                        completed_at=r.get("completed_at"))
                elif r["status"] == "enqueued":
                    t = Task(
                        id=tid, queue=queue_name,
                        payload=r.get("payload", ""),
                        enqueued_by=r.get("enqueued_by", "system"),
                        enqueued_at=r.get("enqueued_at", self._now()),
                        callback=bool(r.get("callback", False)),
                        status="pending")
                    self._all[tid] = t
                    self._pending[queue_name].append(t)
        # Kick dispatch on every queue we just rehydrated.
        for q in list(self._queues):
            self._try_dispatch(q)

    async def stop(self) -> None:
        # Symmetry with start(); nothing to flush in v1 (writes are
        # synchronous on each transition).
        return

    async def _mark_interrupted(self, queue: str, tid: str,
                                last: dict) -> None:
        completed = Task(
            id=tid, queue=queue,
            payload=last.get("payload", ""),
            enqueued_by=last.get("enqueued_by", "system"),
            enqueued_at=last.get("enqueued_at", self._now()),
            callback=bool(last.get("callback", False)),
            status="failed",
            worker_handle=last.get("worker_handle"),
            result=None,
            error="interrupted: aegis restarted mid-flight",
            completed_at=self._now())
        self._all[tid] = completed
        self._log(queue, {
            "event": "failed", "task_id": tid,
            "result": None, "error": completed.error,
            "completed_at": completed.completed_at})
        self._emit(QueueCompleted(
            task_id=tid, queue=queue,
            outcome="interrupted",
            result=None, error=completed.error,
            completed_at=completed.completed_at))
        if completed.callback:
            msg = InboxMessage(
                sender=sender_queue(queue),
                timestamp=self._now(),
                body=completed.error or "",
                task_id=tid,
                status="error")
            await self._inbox.deliver(
                _handle_of(completed.enqueued_by), msg)
