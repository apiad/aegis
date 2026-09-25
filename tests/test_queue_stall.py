"""A transient turn end stalls and rebuilds; it does not close.

Closing is irreversible — the session leaves the roster, its token is
revoked, its pane is dropped. `AgentState.error` covers a dropped SSH link
(the claude driver reports link_lost as `Result(is_error=True)`), a harness
exception, and a stream that ended with no Result, so the old code
destroyed an hour of context on a tunnel blip.
"""
from __future__ import annotations

from aegis.queue.jsonl import read_records
from tests.conftest import make_queue_rig, record_parks, worker_handle


def _log(tmp_path, event):
    return [r for r in read_records(tmp_path / "queues" / "impl.jsonl")
            if r["event"] == event]


def _start(qm, sm, payload="go"):
    """Enqueue, dispatch, and latch the session id a rebuild needs."""
    tid, _ = qm.enqueue("impl", payload, enqueued_by="agent:producer",
                        callback=True)
    h = worker_handle(qm, tid)
    sm.emit_system_init(h, session_id=f"sess-{tid}")
    return tid, h


async def test_a_bad_turn_end_stalls_instead_of_closing(queue_rig, tmp_path):
    qm, sm = queue_rig
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway through")

    assert h not in sm.closed, "the worker must not be closed"
    assert qm.status(tid)["status"] == "dispatched"
    assert _log(tmp_path, "stalled")
    assert sm.reconnected == [h], "the harness under the handle is replaced"


async def test_a_stall_says_nothing_to_the_producer(queue_rig):
    """A blip that recovers in four seconds is not news, and waking a
    producer agent for it costs it a turn."""
    qm, sm = queue_rig
    _tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway")

    assert not sm.inbox_for("producer")


async def test_a_rebuilt_worker_is_told_to_continue(queue_rig):
    """A rebuilt worker has no memory of the interruption; without the
    nudge it sits idle holding the slot."""
    qm, sm = queue_rig
    _tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway", stop_reason="link_lost")

    bodies = [m.body for m in sm.inbox_for(h)]
    assert bodies, "the rebuilt worker was never nudged"
    assert "link_lost" in bodies[-1]
    assert "do not start over" in bodies[-1]


async def test_a_stall_holds_the_max_parallel_slot(queue_rig):
    """Held only for the rebuild window. Releasing it here would run two
    workers for one queue with max_parallel=1."""
    qm, sm = queue_rig
    first, _ = qm.enqueue("impl", "one", enqueued_by="agent:producer",
                          callback=True)
    second, _ = qm.enqueue("impl", "two", enqueued_by="agent:producer",
                           callback=True)
    h = worker_handle(qm, first)
    sm.emit_system_init(h, session_id="sess-1")

    await sm.fail(h, text="halfway")

    assert qm.status(second)["status"] == "pending"


async def test_the_stall_log_carries_the_diagnostics(queue_rig, tmp_path):
    """Recorded to be read, not branched on. This is the evidence for ever
    building a real taxonomy of failure reasons."""
    qm, sm = queue_rig
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway", stop_reason="link_lost")

    rec = _log(tmp_path, "stalled")[0]
    assert rec["stop_reason"] == "link_lost"
    assert rec["attempt"] == 1
    assert rec["last_text"] == "halfway"
    assert rec["task_id"] == tid
    assert rec["worker_handle"] == h


async def test_attempts_accumulate_across_rebuilds(tmp_path):
    """Review Focus 1. Resetting attempts on rebuild is an unbounded retry
    loop that holds the slot forever — the wedged queue this design exists
    to avoid, arriving by the door marked recovery.

    `max_attempts=3`, not the rig default of 2, because two stalls need
    budget for two stalls: on the default the second one is terminal by
    design and the assertion below would be measuring the park path.
    """
    qm, sm = make_queue_rig(tmp_path, max_attempts=3)
    tid, h = _start(qm, sm)

    await sm.fail(h, text="one")
    assert qm._all[tid].attempts == 1

    await sm.fail(h, text="two")
    assert qm._all[tid].attempts == 2


async def test_the_budget_is_spent_on_the_max_attempts_th_stall(tmp_path):
    """Increment-before-classify, read from the park side.

    With `max_attempts=2` the SECOND bad turn end parks and the first does
    not. Classifying on the pre-increment count would push both one stall
    later, so `max_attempts: 1` would retry once instead of parking on the
    first stall — the opposite of what the config documents.

    `_park` lands in Task 8; this stands a recorder in for it, which is
    also the only way to reach the terminal arm from here at all.
    """
    qm, sm = make_queue_rig(tmp_path, max_attempts=2)
    parked: list[tuple[int, str]] = []

    async def record(session, task, *, reason):
        parked.append((task.attempts, reason))

    qm._park = record
    _tid, h = _start(qm, sm)

    await sm.fail(h, text="one")
    assert parked == [], "the first stall of two must still have budget"

    await sm.fail(h, text="two")
    assert [attempts for attempts, _ in parked] == [2]
    assert "exhausted" in parked[0][1]


async def test_a_double_finalize_spends_one_attempt(tmp_path):
    """Review Focus 4. One interruption, two finished-state callbacks — what
    a harness that reports both an error and a stream end does.

    The brief expected the duplicate to find nothing in `_workers` and
    return. It does not: the stall arm deliberately puts the worker BACK, so
    the duplicate read as a second bad turn end, spent a second attempt, and
    on the default budget of 2 parked the worker after exactly one rebuild —
    a single blip using the whole budget. Hence `_stalling`.
    """
    qm, sm = make_queue_rig(tmp_path)
    parked = record_parks(qm)
    tid, h = _start(qm, sm)

    await sm.fail(h, text="halfway", emit_twice=True)

    assert len(_log(tmp_path, "stalled")) == 1
    assert qm._all[tid].attempts == 1
    assert sm.reconnected == [h], "one interruption, one rebuild"
    assert parked == [], "one blip must not spend the whole budget"


async def test_a_worker_waiting_on_a_monitor_defers_not_stalls(queue_rig,
                                                               tmp_path):
    """`_still_working` KEEPS PRECEDENCE over the stall arm.

    Ending a turn is how an agent WAITS. A worker that armed a monitor and
    ended its turn has finished nothing, and rebuilding it would throw
    away the monitor it was waiting on — the wake would have nowhere to
    land. So the deferral runs first and the stall arm never sees this
    turn: no `stalled` record, no rebuild, and `attempts` untouched, so a
    long wait cannot spend the retry budget.

    This test is only worth something because `StubSM` now serves a
    roster and a monitor plane; before that every worker read as "not
    waiting" and this would have passed with the check deleted.
    """
    qm, sm = queue_rig
    tid, h = _start(qm, sm)
    sm.arm_monitor(h)

    await sm.fail(h, text="armed a monitor; ending my turn to wait")

    assert _log(tmp_path, "deferred"), "the wait was not recognised"
    assert not _log(tmp_path, "stalled"), \
        "_still_working must run BEFORE the stall arm"
    assert sm.reconnected == [], "a waiting worker must not be rebuilt"
    assert qm._all[tid].attempts == 0, "a wait must not spend the budget"
    assert h not in sm.closed
    assert qm.status(tid)["status"] == "dispatched"


async def test_a_clean_end_still_completes(queue_rig):
    """The done arm is untouched; this is the regression guard for it."""
    qm, sm = queue_rig
    tid, h = _start(qm, sm)

    await sm.finish(h, text="DONE")

    assert qm.status(tid)["status"] == "completed"
    assert h in sm.closed
