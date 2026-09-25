"""Rebuilding the queue's state from its log after a restart.

Split out of manager.py because `start()` and the branch per task status
are one job and manager.py was already 895 lines of a different one — the
dispatch state machine.

The event-to-status map is the important part. Membership in
`_LIFECYCLE_EVENTS` used to mean "status = event name", and under that rule
a `resumed` record sets a status no branch below matches, which drops the
task out of `_all` with no callback and blocks its producer forever.

What a `dispatched` task means changed with this module. It used to mean
`failed: interrupted` — the task was declared lost and its producer told
so, while the worker's tab came back from `plan_resume` with the whole
conversation in it and nobody ever looked at it again. It now means resume
or park, and never re-run: a worker that got halfway may already have
committed, pushed, deployed or sent mail, so replaying its payload is a
second execution, not a recovery.
"""

from __future__ import annotations

from pathlib import Path

from aegis.core.recovery import NUDGE_RESTART, Resumable, restore
from aegis.queue.manager import _LIFECYCLE_EVENTS
from aegis.queue.schema import Task

#: Lifecycle event name -> the task status it implies. NOT the identity
#: map: a `resumed` record means the task is dispatched again, and a
#: `stalled` one means it never stopped being dispatched.
EVENT_STATUS: dict[str, str] = {
    "enqueued": "pending",
    "dispatched": "dispatched",
    "stalled": "dispatched",
    "resumed": "dispatched",
    "recoverable": "recoverable",
    "completed": "completed",
    "failed": "failed",
}

#: Statuses this module knows how to rebuild. The structural test asserts
#: every value of EVENT_STATUS appears here.
REPLAY_BRANCHES: frozenset[str] = frozenset(
    {"pending", "dispatched", "recoverable", "completed", "failed"}
)


async def replay(qm) -> None:
    """Rebuild every queue's in-memory state from its JSONL log.

    Reads each log into a per-task latest-aggregate view — last lifecycle
    event wins for status, all fields merged so the final dict carries the
    enqueued metadata plus whatever the dispatch, stall and park records
    added — then takes one branch per status.
    """
    if qm._state_dir is None:
        return
    from aegis.queue.jsonl import read_records

    qdir = Path(qm._state_dir) / "queues"
    if not qdir.exists():
        return
    for path in sorted(qdir.glob("*.jsonl")):
        queue_name = path.stem
        if queue_name not in qm._queues:
            # Orphaned log from a removed queue — leave the file
            # untouched; reading other queues' logs is unaffected.
            continue
        tasks: dict[str, dict] = {}
        for rec in read_records(path):
            tid = rec.get("task_id")
            if tid is None:
                continue
            tasks.setdefault(tid, {}).update(rec)
            # `stalled` writes the retry counter as `attempt` and
            # `recoverable` writes it as `attempts`. Merged as two keys
            # they are order-dependent — a task stalled again after being
            # resumed keeps the stale `attempts` — so normalise on the way
            # in and let the merge be plain last-wins over one key. Read
            # from the wrong key the counter is always 0, which is an
            # unbounded retry loop arriving through a typo.
            if "attempt" in rec or "attempts" in rec:
                tasks[tid]["attempts"] = rec.get("attempts", rec.get("attempt"))
            # Only a LIFECYCLE event moves the status. Diagnostic records
            # (`deferred`, `waiting_probe_failed`, `worker_session`) merge
            # their fields and leave the task where it was — a deferred
            # task is still `dispatched`, and replaying it as anything
            # else matches no branch below, so the task disappears from
            # `_all` with no callback. The producer then blocks forever on
            # a task the substrate has forgotten.
            if rec["event"] in _LIFECYCLE_EVENTS:
                tasks[tid]["status"] = EVENT_STATUS[rec["event"]]
        for tid, r in tasks.items():
            status = r.get("status")
            if status == "dispatched":
                await _restore_or_park(qm, queue_name, tid, r)
            elif status in ("recoverable", "completed", "failed"):
                # A parked task stays parked and says nothing. Its
                # producer was told once already, and a second notice on
                # every daemon restart is a turn it has to spend reading
                # something it knows. Rehydrating it at all is what lets
                # the recoverable-TTL reaper see it after a restart.
                qm._all[tid] = _task_from_record(queue_name, tid, r, qm._now)
            elif status == "pending":
                t = _task_from_record(queue_name, tid, r, qm._now)
                qm._all[tid] = t
                qm._pending[queue_name].append(t)
    # Kick dispatch on every queue we just rehydrated.
    for q in list(qm._queues):
        qm._try_dispatch(q)


async def _restore_or_park(qm, queue_name: str, tid: str, r: dict) -> None:
    """A task that was in flight when the process died: put its worker
    back, or park the task with the conversation wherever it is.

    Never a third option. Re-running the payload would be a second
    execution of work that may already have committed, pushed or deployed.
    """
    task = _task_from_record(queue_name, tid, r, qm._now)
    q = qm._queues[queue_name]
    if task.resumable is None or task.attempts >= q.max_attempts:
        await _park(
            qm,
            task,
            reason=(
                "the worker never reached a turn boundary; no conversation to resume"
                if task.resumable is None
                else f"stalled {task.attempts} time(s) before the restart"
            ),
        )
        return
    handle = await restore(qm._sm, task, nudge=NUDGE_RESTART)
    if handle is None:
        await _park(qm, task, reason="could not rebuild after the restart")
        return
    # Ordering matters and there is deliberately no await in here.
    # `restore` delivers the nudge, which schedules the worker's turn as a
    # task rather than running it; populating `_workers` before the loop
    # next yields is what keeps that turn's finalizer from early-returning
    # on a handle the queue does not yet claim.
    qm._all[tid] = task
    qm._inflight[queue_name].append(task)
    qm._workers[handle] = (task, r.get("last_text", ""))
    qm._attach_observers(qm._sm.get(handle), task)
    qm._log(
        queue_name,
        {
            "event": "resumed",
            "task_id": tid,
            "worker_handle": handle,
            "at": qm._now(),
        },
    )


async def _park(qm, task: Task, *, reason: str) -> None:
    """Park a replayed task through the manager's own `_park`.

    The session handed over is whatever actually stands under the task's
    handle, which is usually nothing — the worker died with the process —
    but is a real session when the front end's `plan_resume` restored the
    tab before the queue got here. Passing it matters: `_park` re-origins a
    live session to `parked`, which is what stops `GhostBook` fading the
    conversation the operator was just handed.
    """
    session = qm._session_under(task.worker_handle)
    await qm._park(session, task, reason=reason)


def _task_from_record(queue: str, tid: str, r: dict, now) -> Task:
    """One merged log view -> the Task it describes.

    `attempts` and `resumable` are the two fields a wrong mapping loses
    silently. Zero attempts means the retry budget restarts from scratch on
    every daemon restart, which is an unbounded retry loop holding the
    queue's `max_parallel` slot; a missing `resumable` means every
    interrupted worker parks instead of resuming, which is the loss this
    module exists to remove.
    """
    status = r.get("status", "pending")
    terminal = status in ("completed", "failed", "recoverable")
    return Task(
        id=tid,
        queue=queue,
        payload=r.get("payload", ""),
        enqueued_by=r.get("enqueued_by", "system"),
        enqueued_at=r.get("enqueued_at") or now(),
        callback=bool(r.get("callback", False)),
        status=status,
        worker_handle=r.get("worker_handle"),
        # For a task still in flight the only thing on disk is whatever a
        # `deferred` record kept of the worker's voice — carried so a task
        # that ends up parked can still show the producer what it said.
        result=r.get("result") if terminal else (r.get("last_text") or None),
        error=r.get("error") if terminal else None,
        completed_at=r.get("completed_at") if terminal else None,
        attempts=int(r.get("attempts") or 0),
        resumable=_resumable_from_record(r),
        parked_at=r.get("parked_at"),
    )


def _resumable_from_record(r: dict) -> Resumable | None:
    """The rebuild record, read back off the FIVE FLAT KEYS
    `_record_worker_session` writes — `session_id`, `agent_profile`,
    `provider`, `cwd`, `host` — not a nested object under some key.

    None when the harness never reported a conversation id, which is the
    one case there is genuinely nothing to resume.
    """
    sid = r.get("session_id")
    if not sid:
        return None
    return Resumable(
        session_id=sid,
        agent_profile=r.get("agent_profile") or "",
        provider=r.get("provider") or "",
        cwd=r.get("cwd") or "",
        host=r.get("host") or "local",
    )
