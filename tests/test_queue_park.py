"""Parking promotes a worker out of being disposable.

Leaving EPHEMERAL_KINDS is the whole mechanism, and it buys three
behaviours rather than adding a session state: GhostBook stops reading the
session as a departure and stops fading it after GHOST_TTL, close_guard
starts protecting it like any other session, and plan_resume restores it
across a restart with no special case.
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
