"""Parking promotes a worker out of being disposable.

Leaving EPHEMERAL_KINDS is the whole mechanism, and it buys two behaviours
rather than adding a session state: GhostBook stops reading the session as
a departure and stops fading it after GHOST_TTL, and close_guard starts
protecting it like any other session. Surviving a restart is not one of
them — workspace persistence has no ephemeral filter, so an unparked queue
worker was already being restored.
"""
from __future__ import annotations

from aegis.fleet.models import EPHEMERAL_KINDS
from aegis.queue.jsonl import read_records
from tests.conftest import worker_handle


def _log(tmp_path, event):
    return [r for r in read_records(tmp_path / "queues" / "impl.jsonl")
            if r["event"] == event]


def _start(qm, sm, payload="go", *, session_id="sess-1"):
    """Enqueue, dispatch, and latch the session id a rebuild needs."""
    tid, _ = qm.enqueue("impl", payload, enqueued_by="agent:producer",
                        callback=True)
    h = worker_handle(qm, tid)
    if session_id is not None:
        sm.emit_system_init(h, session_id=session_id)
    return tid, h


def test_parked_is_not_an_ephemeral_kind():
    """If it were, the GhostBook would fade the session after 60 seconds
    and the operator would watch their recovery disappear."""
    assert "parked" not in EPHEMERAL_KINDS


async def test_exhausting_attempts_parks_the_worker(queue_rig_max_attempts_1):
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway")

    assert qm.status(tid)["status"] == "recoverable"
    assert h not in sm.closed, "a parked worker is alive, not closed"
    assert sm.get(h).origin.kind == "parked"


async def test_parking_keeps_the_provenance(queue_rig_max_attempts_1):
    """aegis_task_resume looks the session up by this, and the tab label
    shows it."""
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway")

    origin = sm.get(h).origin
    assert origin.by == "impl"
    assert origin.detail == tid


async def test_parking_frees_the_slot(queue_rig_max_attempts_1):
    qm, sm = queue_rig_max_attempts_1
    first, h = _start(qm, sm, "one")
    second, _ = qm.enqueue("impl", "two", enqueued_by="agent:producer",
                           callback=True)

    await sm.fail(h, text="halfway")

    assert qm.status(second)["status"] == "dispatched"


async def test_parking_tells_the_producer_once_and_actionably(
    queue_rig_max_attempts_1,
):
    """The one message the producer gets that it would not have before,
    and only when somebody has to act."""
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway")

    msgs = sm.inbox_for("producer")
    assert len(msgs) == 1
    body = msgs[0].body
    assert h in body, "names the parked session so it can be read"
    assert tid in body, "names the task so it can be resumed"
    assert "aegis_task_resume" in body


async def test_a_failed_rebuild_parks_and_frees_the_slot(
    queue_rig_rebuild_always_fails,
):
    """Review Focus 2, half two. The ACP loadSession probe can fail at
    runtime; that must park, not raise into the finalizer, and must not
    leak the slot."""
    qm, sm = queue_rig_rebuild_always_fails
    first, h = _start(qm, sm, "one")
    second, _ = qm.enqueue("impl", "two", enqueued_by="agent:producer",
                           callback=True)

    await sm.fail(h, text="halfway")

    assert qm.status(first)["status"] == "recoverable"
    assert qm.status(second)["status"] == "dispatched"


async def test_a_worker_with_no_session_id_parks_immediately(queue_rig):
    """Nothing to rebuild, so the retry budget is irrelevant. It parks with
    an honest message rather than burning attempts on an impossibility."""
    qm, sm = queue_rig
    tid, h = _start(qm, sm, session_id=None)

    await sm.fail(h, text="died before SystemInit")

    assert qm.status(tid)["status"] == "recoverable"
    assert "no conversation to resume" in sm.inbox_for("producer")[0].body
    assert sm.reconnected == [], "there was nothing to rebuild"
    assert qm._all[tid].attempts == 0, \
        "an impossibility must not be reported as a spent retry"


async def test_parking_stamps_an_epoch_for_the_reaper(
    queue_rig_max_attempts_1,
):
    """`completed_at` is an ISO string for humans. The TTL reaper runs on a
    tick and must not parse one every time."""
    import time

    qm, sm = queue_rig_max_attempts_1
    before = time.time()
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway")

    parked = qm._all[tid]
    assert isinstance(parked.parked_at, float)
    assert before <= parked.parked_at <= time.time()


async def test_the_recoverable_record_carries_cost_and_stalled_does_not(
    tmp_path,
):
    """Cost goes on `recoverable`, written once per task, never on
    `stalled`, written once per attempt against cumulative session metrics
    — a summing consumer would count the same turn twice."""
    from tests.conftest import make_queue_rig

    qm, sm = make_queue_rig(tmp_path, max_attempts=2)
    tid, h = _start(qm, sm)

    await sm.fail(h, text="one")      # stalls, rebuilds
    await sm.fail(h, text="two")      # budget spent, parks

    stalled = _log(tmp_path, "stalled")
    assert stalled and all("cost" not in r for r in stalled)
    recoverable = _log(tmp_path, "recoverable")
    assert len(recoverable) == 1
    assert "cost" in recoverable[0]
    assert recoverable[0]["task_id"] == tid


async def test_parking_without_a_live_session_still_frees_the_slot(
    queue_rig_max_attempts_1,
):
    """The shape the restart replay calls: the worker died with the process,
    so there is no session to re-origin. Everything else about parking —
    the record, the log line, the event, the callback, the slot — still
    happens."""
    qm, sm = queue_rig_max_attempts_1
    first, h = _start(qm, sm, "one")
    second, _ = qm.enqueue("impl", "two", enqueued_by="agent:producer",
                           callback=True)

    await qm._park(None, qm._all[first], reason="aegis restarted")

    assert qm.status(first)["status"] == "recoverable"
    assert qm._all[first].worker_handle == h
    assert qm.status(second)["status"] == "dispatched"
    assert sm.get(h).origin.kind != "parked", \
        "no live session was handed in, so none was re-origined"
    assert h in sm.inbox_for("producer")[0].body


async def test_run_reports_parking_as_its_own_status(tmp_path):
    """`aegis_delegate`'s synchronous shape. Told "failed", the caller gives
    up on work that is one `aegis_task_resume` from continuing."""
    import asyncio

    from tests.conftest import make_queue_rig

    qm, sm = make_queue_rig(tmp_path, max_attempts=1)

    async def drive():
        # `run()` enqueues from inside itself, so the handle only exists
        # once it has. One loop turn is enough: dispatch is synchronous.
        while not qm._workers:
            await asyncio.sleep(0)
        h = next(iter(qm._workers))
        sm.emit_system_init(h, session_id="sess-1")
        await sm.fail(h, text="halfway")

    driver = asyncio.create_task(drive())
    res = await asyncio.wait_for(
        qm.run("impl", "go", enqueued_by="agent:boss"), 2)
    await driver

    assert res["status"] == "recoverable"
    assert res["worker_handle"] == qm._all[res["task_id"]].worker_handle
    assert res["worker_handle"] not in sm.closed


async def test_a_double_finalize_after_parking_parks_once(
    queue_rig_max_attempts_1, tmp_path,
):
    """`_park` pops the handle out of `_workers`, and that pop is the only
    thing standing between one interruption and two parks.

    One bad turn end can fire the finished-state callback twice — a harness
    reporting both an error and a stream end does exactly that. The first
    call parks. Without the pop the second finds the worker still in
    `_workers`, still reads as a bad turn end, and parks the same task
    again: a second `recoverable` record with a second copy of the
    cumulative cost, a second `QueueCompleted`, and a second inbox message
    telling the producer to go resume a task it has already been told
    about. `_finalize`'s opening guard is what makes the duplicate a
    no-op, and it can only fire because the pop happened.
    """
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)
    outcomes = []
    qm.subscribe(lambda ev: outcomes.append(getattr(ev, "outcome", None)))

    await sm.fail(h, text="halfway", emit_twice=True)

    recoverable = _log(tmp_path, "recoverable")
    assert len(recoverable) == 1, \
        "the same task was parked twice; its cost is now double-counted"
    assert len(sm.inbox_for("producer")) == 1, \
        "the producer was told twice about one park"
    assert [o for o in outcomes if o == "recoverable"] == ["recoverable"]
    assert qm._all[tid].attempts == 1, "one interruption, one spent attempt"


async def test_cancelling_a_parked_task_leaves_its_session_alive(
    queue_rig_max_attempts_1, tmp_path,
):
    """`recoverable` was absent from cancel's terminal set, so a parked task
    took the in-flight branch and `close`d the conversation parking exists
    to keep — the ephemeral-close arriving through the cancel door.

    Cancelling still WORKS: `recoverable` is not terminal, and a producer
    told to read-or-resume needs a way to decline. It is the session that
    must survive.
    """
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)
    await sm.fail(h, text="halfway")
    assert qm.status(tid)["status"] == "recoverable"

    res = await qm.cancel(tid)

    assert res == {"ok": True, "status": "cancelled", "was": "parked",
                   "worker_handle": h, "session_kept": True}
    assert qm.status(tid)["status"] == "cancelled"
    assert h not in sm.closed, \
        "cancelling the task destroyed the parked conversation"
    assert sm.get(h) is not None, "the session left the roster"
    assert sm.get(h).origin.kind == "parked", \
        "the surviving session is still parked, not re-ephemeralised"


async def test_cancelling_a_parked_task_tells_the_producer_once(
    queue_rig_max_attempts_1,
):
    """One notice per transition. The park notice said "read it or resume
    it"; this one says the queue has let go and the session is still
    there — not a second copy of the first."""
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)
    await sm.fail(h, text="halfway")

    await qm.cancel(tid)

    bodies = [m.body for m in sm.inbox_for("producer")]
    assert len(bodies) == 2, "one park notice, one cancellation notice"
    assert "aegis_task_resume" in bodies[0]
    assert "will not be resumed" in bodies[1]
    assert h in bodies[1], "names the session that survived"
    assert "aegis_task_resume" not in bodies[1], \
        "still offering a resume the cancellation just withdrew"


async def test_cancelling_a_parked_task_does_not_recount_its_cost(
    queue_rig_max_attempts_1, tmp_path,
):
    """`_park` already wrote this worker's spend. The cancellation record
    must not carry a second copy of the same cumulative metrics.

    This one does not die when the parked branch is deleted — the in-flight
    path it falls through to writes an empty cost too. It kills the other
    mutant: `_cancel_parked` growing a `self._cost_dict(...)` call by
    copy-paste from the completion path, which would double this worker's
    tokens for anything summing the log.
    """
    qm, sm = queue_rig_max_attempts_1
    tid, h = _start(qm, sm)
    await sm.fail(h, text="halfway")

    await qm.cancel(tid)

    priced = [r for r in _log(tmp_path, "recoverable") + _log(tmp_path, "failed")
              if r.get("cost")]
    assert len(priced) == 1
    assert priced[0]["event"] == "recoverable"
