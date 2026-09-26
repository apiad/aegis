"""Every lifecycle event maps to a status, and every status has a branch.

Membership in `_LIFECYCLE_EVENTS` used to mean `status = event name`. A
`resumed` record under that rule sets a status of "resumed", which matches
no branch, which drops the task out of `_all` with no callback and blocks
its producer forever. That is a fix for one hang introducing another, and
this file exists so the next person cannot do it by adding one line.
"""
from __future__ import annotations

from aegis.queue.manager import _LIFECYCLE_EVENTS
from aegis.queue.replay import EVENT_STATUS, REPLAY_BRANCHES


def test_every_lifecycle_event_has_a_status():
    missing = _LIFECYCLE_EVENTS - set(EVENT_STATUS)
    assert not missing, (
        f"lifecycle events with no status mapping: {sorted(missing)}. "
        "Without one, replay sets the status to the event name and the "
        "task matches no branch."
    )


def test_every_mapped_status_has_a_replay_branch():
    missing = set(EVENT_STATUS.values()) - REPLAY_BRANCHES
    assert not missing, (
        f"statuses with no replay branch: {sorted(missing)}. A task in one "
        "of these vanishes on restart and its producer waits forever."
    )


def test_resumed_maps_back_to_dispatched():
    assert EVENT_STATUS["resumed"] == "dispatched"


# ---------------------------------------------------------------------------
# The behavioural rigs. Each hand-writes a queue JSONL the way
# tests/test_queue_e2e.py does, then builds a fresh QueueManager over it —
# which is exactly the shape of a daemon booting onto state a dead process
# left behind.
# ---------------------------------------------------------------------------

import itertools

import pytest

from aegis.queue import InboxRouter, QueueManager
from aegis.queue.jsonl import append_record, read_records
from aegis.queue.schema import Queue as QueueSpec
from aegis.queue.schema import sender_agent
from tests.test_queue_e2e import HANG, StubSM

TID = "01TID1"
WORKER = "vivid-laplace"
PRODUCER = "lucid-knuth"

ENQUEUED = {
    "event": "enqueued", "task_id": TID, "queue": "impl", "payload": "go",
    "enqueued_by": f"agent:{PRODUCER}", "enqueued_at": "2026-05-20T07:14:00Z",
    "callback": True,
}
DISPATCHED = {"event": "dispatched", "task_id": TID, "worker_handle": WORKER}


def _worker_session(cwd):
    """What `_record_worker_session` writes: SIX FLAT KEYS, not a nested
    object. A reader that looks for `resumable` finds nothing and parks
    every recoverable task."""
    return {
        "event": "worker_session", "task_id": TID, "worker_handle": WORKER,
        "session_id": "sess-1", "agent_profile": "claude-impl",
        "provider": "claude-code", "cwd": str(cwd), "host": "local",
        "log_id": "log-before-the-crash",
        "at": "2026-05-20T07:15:00Z",
    }


def _stalled(n):
    """What `_finalize` writes on a rebuild. The counter's key here is
    `attempt`, SINGULAR, while the Task field is `attempts`."""
    return {
        "event": "stalled", "task_id": TID, "worker_handle": WORKER,
        "attempt": n, "last_text": "halfway", "stop_reason": "link_lost",
        "error": None, "at": "2026-05-20T07:16:00Z",
    }


def _recoverable(n):
    return {
        "event": "recoverable", "task_id": TID, "worker_handle": WORKER,
        "attempts": n, "error": "stalled twice; attempts exhausted",
        "completed_at": "2026-05-20T07:17:00Z", "parked_at": 1779000000.0,
        "cost": {},
    }


def _rig(tmp_path, records, *, max_attempts=2):
    for rec in records:
        append_record(tmp_path / "queues" / "impl.jsonl", rec)
    sm = StubSM(autostart=False)
    # A restored worker is mid-task: it must not run a scripted turn to
    # completion the moment the nudge wakes it, or every assertion below
    # measures a completion instead of a recovery.
    sm.script(WORKER, HANG)
    inbox = InboxRouter(state_dir=tmp_path)
    sm.inbox = inbox
    minted = itertools.count(1)
    qm = QueueManager(
        {"impl": QueueSpec(name="impl", agent_profile="claude-impl",
                           max_parallel=1, max_attempts=max_attempts)},
        sm, inbox, state_dir=tmp_path,
        handle_factory=lambda used: f"w{next(minted)}",
    )
    return qm, sm, tmp_path


@pytest.fixture
def replay_rig(tmp_path):
    """In flight at the crash, one attempt left, a conversation to resume."""
    return _rig(tmp_path, [ENQUEUED, DISPATCHED, _worker_session(tmp_path)])


@pytest.fixture
def replay_rig_no_worker_session(tmp_path):
    """The harness died before reporting a session id."""
    return _rig(tmp_path, [ENQUEUED, DISPATCHED])


@pytest.fixture
def replay_rig_exhausted(tmp_path):
    return _rig(
        tmp_path,
        [ENQUEUED, DISPATCHED, _worker_session(tmp_path), _stalled(2)],
        max_attempts=2,
    )


@pytest.fixture
def replay_rig_recoverable(tmp_path):
    return _rig(
        tmp_path,
        [ENQUEUED, DISPATCHED, _worker_session(tmp_path), _stalled(2),
         _recoverable(2)],
    )


@pytest.fixture
def replay_rig_pending(tmp_path):
    return _rig(tmp_path, [ENQUEUED])


async def test_restart_adopts_a_session_plan_resume_already_restored(
    replay_rig,
):
    """A queue worker is an ordinary tab in workspace.json and plan_resume
    does not filter by origin, so at boot the front end restores the
    worker AND the replay wants it. Whichever runs second would mount a
    second pane under a held handle: DuplicateIds, the whole app. So the
    replay looks before it builds."""
    qm, sm, tmp_path = replay_rig
    sm.preload_session(WORKER, session_id="sess-1")
    before = len(sm.list_sessions())

    await qm.start()

    assert len(sm.list_sessions()) == before, "must adopt, not spawn"
    assert sm.spawned == [], "must adopt, not spawn"
    assert sm.reconnected == [WORKER], "the dead harness was not replaced"
    assert qm.status(TID)["status"] == "dispatched"


async def test_restart_rebuilds_when_the_handle_is_free(replay_rig):
    qm, sm, tmp_path = replay_rig

    await qm.start()

    assert sm.get(WORKER) is not None
    assert sm.spawned == [WORKER]
    assert sm.resumed_from == "sess-1", "rebuilt without resuming the talk"
    assert qm.status(TID)["status"] == "dispatched"


async def test_a_cold_restore_keeps_the_worker_s_transcript(replay_rig):
    """The spec: "Only when the handle is unoccupied does it spawn with
    `resume_from` and the recorded `log_id`."

    `AgentSession.__init__` is `log_id or new_log_id(handle)`, so a restore
    that drops it starts a BRAND-NEW transcript — and `read_peer` windows
    the on-disk log by `log_id`. This is the normal headless path, so the
    consequence was routine: restart, cold restore, the worker later parks,
    the park notice tells the producer to `aegis_read_peer(<handle>)`, and
    the producer sees only turns since the restart. Everything from before
    is on disk under an orphaned log id nothing references.
    """
    qm, sm, tmp_path = replay_rig

    await qm.start()

    assert sm.spawned == [WORKER], "the cold branch, not the adopt branch"
    assert sm.spawned_log_id == "log-before-the-crash"


async def test_the_resumable_read_back_off_the_log_carries_the_log_id(tmp_path):
    """The record on disk is the only thing a restart has. A `Resumable`
    rebuilt without `log_id` makes the test above unfalsifiable from the
    replay side."""
    from aegis.queue.replay import _resumable_from_record

    r = _resumable_from_record(_worker_session(tmp_path))
    assert r is not None
    assert r.log_id == "log-before-the-crash"
    # And None, not "", for a record written before the key existed: an
    # empty string is a log id `AgentSession` would take at face value.
    old = _worker_session(tmp_path)
    del old["log_id"]
    assert _resumable_from_record(old).log_id is None


async def test_a_rebuilt_worker_is_told_its_conversation_survived(replay_rig):
    """It has no memory of the interruption — from inside, the turn simply
    never ended — so a nudge that does not say so has it re-derive what it
    already knows, or start over."""
    qm, sm, tmp_path = replay_rig

    await qm.start()

    bodies = [m.body for m in sm.inbox_for(WORKER)]
    assert bodies, "the rebuilt worker was never told anything"
    assert "aegis restarted" in bodies[-1]
    assert "do not start over" in bodies[-1]


async def test_a_restored_task_keeps_the_slot_and_logs_resumed(replay_rig):
    """`resumed` is the record whose status mapping this file exists for,
    and holding the in-flight slot is what stops the queue dispatching a
    second worker over the top of the one just recovered."""
    qm, sm, tmp_path = replay_rig

    await qm.start()

    assert [t.id for t in qm._inflight["impl"]] == [TID]
    log = read_records(tmp_path / "queues" / "impl.jsonl")
    assert log[-1]["event"] == "resumed"
    assert log[-1]["worker_handle"] == WORKER


async def test_restart_parks_a_task_with_no_session_record(
    replay_rig_no_worker_session,
):
    """Never re-run from the payload: a worker that got halfway may have
    committed, pushed or deployed, and re-running its prompt is a second
    execution, not a recovery."""
    qm, sm, tmp_path = replay_rig_no_worker_session

    await qm.start()

    assert qm.status(TID)["status"] == "recoverable"
    assert not sm.spawned, "the payload must not be re-run"


async def test_restart_parks_a_task_whose_attempts_are_exhausted(
    replay_rig_exhausted,
):
    qm, sm, tmp_path = replay_rig_exhausted
    await qm.start()
    assert qm.status(TID)["status"] == "recoverable"
    assert not sm.spawned, "the retry budget was spent before the restart"


async def test_the_retry_budget_survives_the_restart(replay_rig_exhausted):
    """`stalled` writes the counter as `attempt`, singular. Read from the
    wrong key it is always 0, every restart hands the task a fresh budget,
    and a worker that cannot be rebuilt is rebuilt forever while holding
    the queue's only slot."""
    qm, sm, tmp_path = replay_rig_exhausted

    await qm.start()

    assert qm._all[TID].attempts == 2
    assert "2 time(s)" in (qm.status(TID)["error"] or "")


async def test_a_recoverable_task_stays_put_and_says_nothing(
    replay_rig_recoverable,
):
    """The producer was told once already; a second message on every
    daemon restart is noise it has to spend a turn reading."""
    qm, sm, tmp_path = replay_rig_recoverable

    await qm.start()

    assert qm.status(TID)["status"] == "recoverable"
    assert not sm.inbox_for(PRODUCER)


async def test_a_parked_task_keeps_its_parked_at_across_a_restart(
    replay_rig_recoverable,
):
    """The recoverable-TTL reaper reads `parked_at`. Rehydrated as None it
    never sees a task parked before the restart, so the oldest parked
    sessions are exactly the ones it cannot reap."""
    qm, sm, tmp_path = replay_rig_recoverable

    await qm.start()

    assert qm._all[TID].parked_at == 1779000000.0


async def test_pending_is_still_requeued_at_head_of_fifo(replay_rig_pending):
    qm, sm, tmp_path = replay_rig_pending
    await qm.start()
    assert qm.status(TID)["status"] in ("pending", "dispatched")


async def test_a_worker_with_no_cwd_parks_rather_than_guessing(tmp_path):
    """`Resumable.cwd` defaults to "" and a spawn with `cwd=None` lands in
    the DEFAULT tree, not the one the worker was working in. A resumed
    worker committing or deploying in the wrong directory does real
    damage, so an unusable record parks."""
    rec = _worker_session(tmp_path) | {"cwd": ""}
    qm, sm, _ = _rig(tmp_path, [ENQUEUED, DISPATCHED, rec])

    await qm.start()

    assert qm.status(TID)["status"] == "recoverable"
    assert not sm.spawned


async def test_a_parked_worker_the_front_end_restored_is_re_origined(
    replay_rig_exhausted,
):
    """The orphan this change removes: the restart left the worker's tab
    alive with its whole conversation while the queue declared the task
    failed and never looked at it again. Parking onto the session that is
    actually there is what stops GhostBook fading it."""
    qm, sm, tmp_path = replay_rig_exhausted
    sm.preload_session(WORKER, session_id="sess-1")

    await qm.start()

    assert sm.get(WORKER).origin.kind == "parked"
    body = sm.inbox_for(PRODUCER)[0].body
    assert WORKER in body, "the producer was not told where to read it"


async def test_a_park_with_nothing_under_the_handle_says_so(
    replay_rig_no_worker_session,
):
    """Telling a producer to `aegis_read_peer` a handle that holds nothing
    costs it a turn and a wrong conclusion."""
    qm, sm, tmp_path = replay_rig_no_worker_session

    await qm.start()

    body = sm.inbox_for(PRODUCER)[0].body
    assert "aegis_read_peer" not in body
    assert "did not survive" in body
