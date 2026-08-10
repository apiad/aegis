"""A queue worker that armed a waker must survive ending its turn.

The bug, paid for on 2026-08-10 in `repos/ainbox`. A worker took the
warden write-lock-deadlock task, did the work, armed an `aegis_monitor`
on the test suite, said "I'll report when it lands" and ended its turn —
which is exactly the protocol the monitor briefing prescribes.
`QueueManager._finalize` fired on that turn boundary and closed it.

Three things broke at once, and all three are the same bug:

- the monitor's wake had nowhere to land, so the suite result was never
  read and the warden half was left **uncommitted** in a shared checkout
  that Alex and other agents were writing to;
- the callback the producer received was the literal string "Waiting on
  the warden suite — I'll report when it lands", which is not a result;
- the task was marked `completed`, so nothing downstream knew to wait.

`aegis_close` has refused exactly this since it shipped — "no live
monitors, no pending reminders, nothing undelivered in its inbox, no
armed loop". The queue's own teardown never asked.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.events import AssistantText, Result
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

from tests.test_queue_manager import StubSessionManager


WAITING = [AssistantText(text="Waiting on the warden suite — "
                              "I'll report when it lands"),
           Result(duration_ms=1, is_error=False, usage=None)]


class FakeMonitors:
    """The `monitor_manager` plane, as `aegis_close` reads it."""

    def __init__(self, armed: dict[str, int] | None = None):
        self.armed = dict(armed or {})

    def snapshot(self, *, for_handle=None):
        return [object()] * self.armed.get(for_handle, 0)


class FakeReminders:
    def __init__(self, armed=None):
        self.armed = dict(armed or {})

    def list_reminders(self, *, from_handle=None):
        return [object()] * self.armed.get(from_handle, 0)


class FakeLocks:
    def __init__(self, claims=()):
        self._claims = list(claims)

    def active(self):
        return self._claims


class _Claim:
    def __init__(self, handle):
        self.handle = handle


class WaitingSM(StubSessionManager):
    """A stub session manager that also exposes the coordination planes.

    The bridge `QueueManager` holds *is* the object `aegis_close` gathers
    from, so the planes hang off the same seam.
    """

    def __init__(self, monitors=None, reminders=None, locks=None):
        super().__init__()
        self.monitor_manager = FakeMonitors(monitors)
        self.reminder_service = FakeReminders(reminders)
        self.locks = FakeLocks(locks or ())


def _q(name="impl", profile="claude-impl", cap=2):
    return Queue(name=name, agent_profile=profile, max_parallel=cap)


async def _run(sm, *, handle="w1", payload="verify the deadlock fix"):
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: handle)
    tid, _ = qm.enqueue("impl", payload,
                        enqueued_by=sender_agent("producer"), callback=True)
    sm.script(handle, WAITING)
    for _ in range(60):
        await asyncio.sleep(0)
    return qm, inbox, tid


# ---------- the bug ------------------------------------------------------

@pytest.mark.asyncio
async def test_a_worker_with_a_live_monitor_is_not_closed():
    sm = WaitingSM(monitors={"w1": 1})
    await _run(sm)
    assert sm.closed == [], (
        "the worker armed a monitor and ended its turn — closing it "
        "orphans the wake it is waiting for")


@pytest.mark.asyncio
async def test_its_task_is_not_marked_completed():
    sm = WaitingSM(monitors={"w1": 1})
    qm, _, tid = await _run(sm)
    assert qm.status(tid)["status"] != "completed"


@pytest.mark.asyncio
async def test_the_producer_gets_no_callback_yet():
    """"I'll report when it lands" is a promise, not a result."""
    sm = WaitingSM(monitors={"w1": 1})
    _, inbox, _ = await _run(sm)
    assert not inbox.pending("producer")


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"monitors": {"w1": 1}},
                                {"reminders": {"w1": 1}}])
async def test_every_self_terminating_waker_defers(kw):
    sm = WaitingSM(**kw)
    await _run(sm)
    assert sm.closed == []


@pytest.mark.asyncio
async def test_a_pending_inbox_message_defers():
    """Delivered but not yet consumed: it resolves at the next turn
    boundary, so the worker is not done."""
    sm = WaitingSM()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: "w1")
    qm.enqueue("impl", "go", enqueued_by=sender_agent("producer"),
               callback=True)
    sm.script("w1", WAITING)
    sm.inbox_router = inbox
    from aegis.queue.schema import InboxMessage, now_iso, sender_user
    inbox._pending.setdefault("w1", []).append(
        InboxMessage(sender=sender_user(), timestamp=now_iso(), body="wait"))
    for _ in range(60):
        await asyncio.sleep(0)
    assert sm.closed == []


# ---------- and then it finishes ----------------------------------------

@pytest.mark.asyncio
async def test_it_finalizes_on_the_turn_after_the_waker_clears():
    """Deferral is not a leak: the monitor's wake starts another turn, and
    that turn's boundary finalizes normally."""
    sm = WaitingSM(monitors={"w1": 1})
    qm, inbox, tid = await _run(sm)
    assert sm.closed == []

    # The monitor fires; the worker wakes, takes its reporting turn, and
    # that turn ends. Driven straight at the finalizer because the timing
    # of a second scripted turn against `create_task` is not
    # deterministic — the observer wiring itself is covered by every test
    # above, which reach `_finalize` only through it.
    from aegis.tui.state import AgentState
    sm.monitor_manager.armed["w1"] = 0
    session = sm.spawns[0][3]
    await qm._finalize(session, AgentState.ready)
    assert sm.closed == ["w1"]
    assert qm.status(tid)["status"] == "completed"
    assert inbox.pending("producer")


# ---------- what must NOT defer -----------------------------------------

@pytest.mark.asyncio
async def test_a_held_file_claim_does_not_defer():
    """`aegis_close` refuses on a held claim, and is right to: a human is
    asking. Here nobody is — a worker that forgot to release would pin a
    max_parallel slot forever, and claims auto-reap on close anyway."""
    sm = WaitingSM(locks=[_Claim("w1")])
    await _run(sm)
    assert sm.closed == ["w1"]


# ---------- the deferred record must replay ------------------------------

@pytest.mark.asyncio
async def test_a_deferred_task_replays_as_interrupted_not_dropped(tmp_path):
    """Boot replay keys on the last event's name, so a diagnostic record
    that is not a lifecycle event silently steals the task's status.

    A task whose log ends at `deferred` was in flight when the process
    died. Matching no branch, it vanishes from `_all` entirely: no
    `failed:interrupted`, no callback, and a producer blocked forever on
    a task the substrate has forgotten. Adding the record without this is
    how the fix for one hang introduces another.
    """
    from aegis.queue.jsonl import append_record

    qdir = tmp_path / "queues"
    log = qdir / "impl.jsonl"
    for rec in (
        {"event": "enqueued", "task_id": "t1", "payload": "go",
         "enqueued_by": "agent:producer", "callback": True},
        {"event": "dispatched", "task_id": "t1", "worker_handle": "w1"},
        {"event": "deferred", "task_id": "t1", "worker_handle": "w1",
         "waiting_on": ["1 live monitor(s) still watching"]},
    ):
        append_record(log, rec)

    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, StubSessionManager(), inbox,
                      state_dir=tmp_path)
    await qm.start()

    st = qm.status("t1")
    assert st is not None, "the task was dropped on replay"
    assert st["status"] == "failed"
    assert inbox.pending("producer"), "the producer never got its callback"


@pytest.mark.asyncio
async def test_a_bridge_with_no_coordination_planes_still_finalizes():
    """The planes are read with getattr. A frontend without them must get
    today's behaviour, not a queue that never completes anything."""
    sm = StubSessionManager()
    sm.script("w1", WAITING)
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: "w1")
    tid, _ = qm.enqueue("impl", "go", enqueued_by=sender_agent("producer"),
                        callback=True)
    for _ in range(60):
        await asyncio.sleep(0)
    assert sm.closed == ["w1"]
    assert qm.status(tid)["status"] == "completed"
