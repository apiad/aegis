from __future__ import annotations

import asyncio

from aegis.queue import InboxRouter, QueueManager, sender_agent
from aegis.queue.events import (
    QueueCompleted,
    QueueDispatched,
    QueueEnqueued,
    QueueStarted,
)

from tests.test_queue_manager import StubSessionManager, _q


async def _drain_until(received, predicate, ticks=50):
    for _ in range(ticks):
        await asyncio.sleep(0)
        if predicate(received):
            return


async def test_subscribe_receives_full_lifecycle():
    received: list = []
    sm, inbox = StubSessionManager(), InboxRouter()
    qm = QueueManager(
        {"tasks": _q(name="tasks", profile="claude", cap=1)},
        sm, inbox,
        handle_factory=lambda used: "worker-1",
    )
    unsub = qm.subscribe(lambda ev: received.append(ev))

    tid, _ = qm.enqueue(
        "tasks", "payload-1",
        enqueued_by=sender_agent("caller"), callback=False,
    )
    await _drain_until(
        received,
        lambda r: any(isinstance(e, QueueCompleted) for e in r),
    )

    kinds = [type(e).__name__ for e in received]
    assert kinds == [
        "QueueEnqueued", "QueueDispatched",
        "QueueStarted", "QueueCompleted",
    ]
    enq, disp, started, comp = received
    assert isinstance(enq, QueueEnqueued)
    assert enq.task_id == tid and enq.queue == "tasks"
    assert isinstance(disp, QueueDispatched)
    assert disp.worker_handle == "worker-1"
    assert disp.agent_slug == "claude"
    assert isinstance(started, QueueStarted)
    assert started.task_id == tid
    assert isinstance(comp, QueueCompleted)
    assert comp.outcome == "completed"

    unsub()
    qm.enqueue(
        "tasks", "payload-2",
        enqueued_by=sender_agent("caller"), callback=False,
    )
    # Let the second task fully flow through; nothing should be appended.
    for _ in range(50):
        await asyncio.sleep(0)
    assert [type(e).__name__ for e in received] == [
        "QueueEnqueued", "QueueDispatched",
        "QueueStarted", "QueueCompleted",
    ]


async def test_observer_exceptions_do_not_break_substrate():
    sm, inbox = StubSessionManager(), InboxRouter()
    qm = QueueManager(
        {"tasks": _q(name="tasks", profile="claude", cap=1)},
        sm, inbox,
        handle_factory=lambda used: "worker-1",
    )

    def angry(_ev):
        raise RuntimeError("nope")

    received: list = []
    qm.subscribe(angry)
    qm.subscribe(lambda ev: received.append(ev))
    qm.enqueue(
        "tasks", "p",
        enqueued_by=sender_agent("caller"), callback=False,
    )
    # Wait for full lifecycle to flow through. The angry observer should
    # not stop the second observer from receiving events.
    await _drain_until(
        received,
        lambda r: any(isinstance(e, QueueCompleted) for e in r),
    )
    kinds = [type(e).__name__ for e in received]
    assert kinds[:3] == [
        "QueueEnqueued", "QueueDispatched", "QueueStarted",
    ]
    assert "QueueCompleted" in kinds
