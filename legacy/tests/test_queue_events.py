from aegis.queue.events import (
    QueueEnqueued, QueueDispatched, QueueStarted, QueueCompleted,
    QueueEvent,
)


def test_event_construction_and_taxonomy():
    e1 = QueueEnqueued(task_id="t1", queue="tasks",
                       payload="hi", enqueued_by="agent:foo")
    e2 = QueueDispatched(task_id="t1", queue="tasks",
                         worker_handle="brisk-curie",
                         agent_slug="gemini-flash")
    e3 = QueueStarted(task_id="t1", queue="tasks")
    e4 = QueueCompleted(task_id="t1", queue="tasks",
                        outcome="completed",
                        result="ok", error=None,
                        completed_at="2026-05-21T12:00:00Z")
    for e in (e1, e2, e3, e4):
        assert isinstance(e, QueueEvent)
    assert e1.task_id == "t1"
    assert e2.worker_handle == "brisk-curie"
    assert e4.outcome == "completed"
