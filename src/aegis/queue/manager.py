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


# The events that define where a task IS. Everything else a queue log
# carries is diagnostic and must not move the task's status on replay —
# see the comment in `start()`.
_LIFECYCLE_EVENTS = frozenset({"enqueued", "dispatched", "completed",
                               "failed"})

# "no assistant-text run is open for this worker". Distinct from a run
# whose message_id is None, which is a real run (the pre-slice-2 claude
# case, where chunks carry no id and adjacency is the only signal).
_NO_RUN = object()


def _with_last_message(headline: str, last_text: str, *, none_note: str
                       ) -> str:
    """A callback body that carries what the worker actually said.

    The task result IS the worker's final assistant text — that is the
    contract ``aegis_enqueue`` sells. When a worker ends some way other
    than finishing (cancelled, or interrupted by a restart), the outcome
    alone is not a substitute: a worker that had done twenty minutes of
    work and said so was reduced to the string "cancelled", and the
    producer could not tell that anything had happened at all.

    The headline stays first so a producer reading only the opening words
    still learns the task did not finish, and ``none_note`` is why this
    takes an explicit one rather than defaulting to silence — an empty
    body in an inbox reads as a message that failed to render, and
    inventing a quote for a worker that said nothing is worse.
    """
    text = (last_text or "").strip()
    if not text:
        return f"{headline} ({none_note})"
    return f"{headline} — the worker's last message was:\n\n{text}"


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
    has a delivery target in v1; others (system/queue:…) deliver
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
        # Worker names must come out of the same registry as every other
        # handle: a queue worker minted onto a retired name is the same
        # DuplicateIds crash, arriving from the substrate instead of the
        # keyboard. Fall back to the bare generator only for the handful of
        # test doubles that stand in for a session manager.
        registry = getattr(session_manager, "handles", None)
        self._handle_factory = (handle_factory
                                or (registry.mint if registry is not None
                                    else generate_name))
        # in-memory state
        self._pending: dict[str, list[Task]] = {q: [] for q in self._queues}
        self._inflight: dict[str, list[Task]] = {q: [] for q in self._queues}
        self._all: dict[str, Task] = {}
        # per-worker result accumulators: handle -> (task, last_assistant_text)
        self._workers: dict[str, tuple[Task, str]] = {}
        # worker handle -> the message_id of the assistant-text run
        # currently open for it, or _NO_RUN. Kept beside _workers rather
        # than inside its tuple so the (task, last_text) shape every
        # other call site unpacks stays a 2-tuple.
        self._chunk_run: dict[str, object] = {}
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

    async def cancel(self, task_id: str) -> dict:
        """Cancel a task. Pending → dropped from the FIFO; in-flight → the
        worker is interrupted and closed. Marks the task ``cancelled`` and,
        if it had a callback, delivers one error notice to the producer so an
        awaiting caller unblocks. Idempotent for already-terminal tasks."""
        t = self._all.get(task_id)
        if t is None:
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        if t.status in ("completed", "failed", "cancelled"):
            return {"ok": True, "status": t.status, "note": "already terminal"}

        worker_handle = t.worker_handle
        last_text = ""
        if t.status == "pending":
            self._pending[t.queue] = [
                x for x in self._pending[t.queue] if x.id != task_id]
        else:  # dispatched / in-flight
            # Pop from _workers first so any finalize the close triggers
            # early-returns and can't overwrite the cancelled status —
            # but read its last text on the way out. Cancelling is not a
            # reason to throw away everything the worker had already said.
            _, last_text = self._workers.pop(worker_handle, (None, ""))
            self._chunk_run.pop(worker_handle, None)
            self._inflight[t.queue] = [
                x for x in self._inflight[t.queue] if x.id != task_id]

        body = _with_last_message(
            "cancelled", last_text,
            none_note=("never dispatched" if t.status == "pending"
                       else "the worker had not said anything yet"))
        cancelled = Task(**{**t.__dict__,
                            "status": "cancelled",
                            "result": last_text or None,
                            "completed_at": self._now()})
        self._all[task_id] = cancelled
        self._log(t.queue, {
            "event": "failed", "task_id": task_id,
            "result": cancelled.result, "error": "cancelled",
            "completed_at": cancelled.completed_at, "cost": {}})
        self._emit(QueueCompleted(
            task_id=task_id, queue=t.queue, outcome="interrupted",
            result=cancelled.result, error="cancelled",
            completed_at=cancelled.completed_at))
        if t.callback:
            msg = InboxMessage(
                sender=sender_queue(t.queue),
                timestamp=self._now(),
                body=body,
                task_id=task_id,
                status="error")
            await self._inbox.deliver(_handle_of(t.enqueued_by), msg)

        if worker_handle is not None and t.status != "pending":
            with contextlib.suppress(Exception):
                # drain=False: the worker is closed on the next line — waking
                # it with its own backlog first would be pointless.
                await self._sm.interrupt(worker_handle, drain=False)
            with contextlib.suppress(Exception):
                await self._sm.close(worker_handle)
            self._try_dispatch(t.queue)
        return {"ok": True, "status": "cancelled",
                "was": ("pending" if t.status == "pending" else "in_flight")}

    async def run(self, queue: str, payload: str, *,
                  enqueued_by: str,
                  timeout: float | None = None) -> dict:
        """Enqueue a task and await its terminal result — the synchronous
        shape of ``enqueue`` + wait, for callers that want the result
        returned directly rather than as an inbox callback.

        Composes on the existing primitives: enqueues with ``callback=False``
        (the result is the return value, not an inbox message) and resolves
        on a one-shot completion subscription — no polling. Returns
        ``{task_id, status, result?, error?}`` where status is
        ``completed`` / ``failed``. Unknown queue → ``{"error": …}``.

        With ``timeout`` set, gives up after that many seconds and returns
        ``{task_id, status: "timeout"}`` — the worker keeps running (use
        ``cancel(task_id)`` to stop it).
        """
        if queue not in self._queues:
            return {"error": f"unknown queue {queue!r}; known: {self.list_queues()}"}
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        target = {"id": None}

        def _obs(ev: QueueEvent) -> None:
            if (isinstance(ev, QueueCompleted)
                    and ev.task_id == target["id"]
                    and not fut.done()):
                fut.set_result(ev)

        unsub = self.subscribe(_obs)
        try:
            result = self.enqueue(queue, payload,
                                  enqueued_by=enqueued_by, callback=False)
            if isinstance(result, dict):   # budget rejection etc.
                return result
            tid, _pos = result
            target["id"] = tid
            try:
                if timeout is not None:
                    ev = await asyncio.wait_for(fut, timeout)
                else:
                    ev = await fut
            except asyncio.TimeoutError:
                return {"task_id": tid, "status": "timeout"}
            status = "completed" if ev.outcome == "completed" else "failed"
            return {"task_id": tid, "status": status,
                    "result": ev.result, "error": ev.error}
        finally:
            unsub()

    def worker_label(self, handle: str) -> str | None:
        """``<queue>#<short-id>`` for a currently in-flight worker handle,
        else None. The TUI suffixes worker tabs with this while they run
        so a background worker is legible at a glance."""
        entry = self._workers.get(handle)
        if entry is None:
            return None
        task = entry[0]
        return f"{task.queue}#{task.id[-4:]}"

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
            h = session.handle
            if isinstance(ev, AssistantText):
                if h not in self._workers:
                    return
                # A subagent's narration is not the worker's answer.
                # `capture_next_reply` states the rule — "a peer that runs
                # a Task must not fold its subagent's commentary into the
                # answer the operator reads" — and the queue was the one
                # capture path that never applied it, so a producer's
                # callback could be the subagent talking. Skipped rather
                # than treated as an intervening event: ending the run
                # here would truncate the worker's own message whenever a
                # subagent spoke mid-stream. The digest hook below sits
                # behind this too, so the dashboard tail is the worker's
                # own voice rather than its subagents' interleaved.
                if getattr(ev, "parent_tool_use_id", None) is not None:
                    return
                t, last = self._workers[h]
                # Assistant text arrives as a TOKEN STREAM — one message
                # is many events, which is the whole reason
                # `render.coalesce_chunks` exists. Overwriting on each
                # one captured the last *chunk*, so a worker that ended
                # with "Fixed the deadlock in storage.py; suite is
                # green." reported back to its producer as "green.".
                #
                # Same run rule as coalesce_chunks: adjacent events with
                # equal message_id are one message (equal includes both
                # None, the pre-slice-2 claude case), and any other event
                # ends the run — without that, an id-less driver would
                # concatenate the worker's entire monologue.
                mid = getattr(ev, "message_id", None)
                run = self._chunk_run.get(h, _NO_RUN)
                same = run is not _NO_RUN and run == mid
                self._workers[h] = (t, (last + ev.text) if same else ev.text)
                self._chunk_run[h] = mid
                if self._assistant_text_hook is not None:
                    try:
                        # The raw chunk, deliberately: the digest keeps a
                        # rolling tail of fragments, and feeding it the
                        # accumulation would show "Fixed", "Fixed the",
                        # "Fixed the deadlock", …
                        self._assistant_text_hook(h, ev.text)
                    except Exception:  # noqa: BLE001
                        pass
            else:
                self._chunk_run[h] = _NO_RUN

        def on_state(_s, st, finished):
            if not finished:
                return
            asyncio.create_task(self._finalize(session, st))

        session.add_event_observer(on_event)
        session.add_state_observer(on_state)

    def _still_working(self, handle: str, st) -> list[str]:
        """Why this worker's turn ending does not mean it is finished.

        **Ending a turn is how an agent waits.** The monitor briefing says
        so outright — "returns {monitor_id} immediately; END YOUR TURN" —
        so a turn boundary on its own carries no information about whether
        the work is done, and treating it as completion is what closed a
        worker mid-wait on 2026-08-10: its monitor's wake had nowhere to
        land, the producer's callback was the string "I'll report when it
        lands", and the real work sat uncommitted in a shared checkout.

        The planes are read through the same ``gather_facts`` that
        ``aegis_close`` uses, so the substrate cannot drift from the tool
        that has been refusing exactly this since it shipped.
        """
        from aegis.core.close_guard import gather_facts, still_working_reasons
        try:
            facts = gather_facts(self._sm, handle,
                                 state=getattr(st, "value", str(st)))
        except Exception as e:  # noqa: BLE001 — never strand a task on a probe
            # Logged, not swallowed. This handler already hid the fix
            # once: `gather_facts` raised AttributeError on a bridge with
            # no `list_sessions`, every worker read as "not waiting", and
            # the change looked inert against its own failing tests.
            task = self._workers.get(handle, (None, ""))[0]
            if task is not None:
                self._log(task.queue, {
                    "event": "waiting_probe_failed", "task_id": task.id,
                    "worker_handle": handle,
                    "error": f"{type(e).__name__}: {e}", "at": self._now()})
            return []
        return still_working_reasons(facts)

    async def _finalize(self, session, st) -> None:
        if session.handle not in self._workers:
            return
        waiting = self._still_working(session.handle, st)
        if waiting:
            # Leave the task in flight and the worker alive. The thing it
            # is waiting on wakes it, that turn ends, and this runs again
            # — every deferring condition is self-terminating, which is
            # why `claims` is deliberately not one of them.
            task, said = self._workers[session.handle]
            self._log(task.queue, {
                "event": "deferred", "task_id": task.id,
                "worker_handle": session.handle,
                "waiting_on": waiting, "at": self._now(),
                # The only point at which a live worker's words reach
                # disk. If the process dies while it is waiting, this is
                # all `_mark_interrupted` will have to hand the producer.
                "last_text": said})
            return
        task, last_text = self._workers.pop(session.handle)
        self._chunk_run.pop(session.handle, None)
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
            # A worker can finish cleanly having emitted only tool calls.
            # An empty body in an inbox reads as a message that failed to
            # render, so say what happened rather than nothing.
            body = (result or "") if ok else (error or "")
            if not body.strip():
                body = ("the worker finished without a final message"
                        if ok else "the worker exited with no message")
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
                # Only a LIFECYCLE event moves the status. Diagnostic
                # records (`deferred`, `waiting_probe_failed`) merge their
                # fields and leave the task where it was — a deferred task
                # is still `dispatched`, and replaying it as anything else
                # matches no branch below, so the task disappears from
                # `_all` with no `failed:interrupted` and no callback. The
                # producer then blocks forever on a task the substrate has
                # forgotten: the fix for one hang, introducing another.
                if rec["event"] in _LIFECYCLE_EVENTS:
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
            result=last.get("last_text") or None,
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
                # Whatever the log kept of the worker before the process
                # died — written by the `deferred` record, so a worker
                # that was waiting has words here and one that never
                # reached a turn boundary honestly has none.
                body=_with_last_message(
                    completed.error or "interrupted", last.get("last_text", ""),
                    none_note="nothing of the worker survived the restart"),
                task_id=tid,
                status="error")
            await self._inbox.deliver(
                _handle_of(completed.enqueued_by), msg)
