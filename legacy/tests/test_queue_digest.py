from datetime import datetime, timezone

from aegis.queue.digest import QueueDigest, QueueView, TaskView
from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueStarted,
)
from aegis.queue.schema import Queue


class _StubManager:
    def __init__(self, queues):
        self._queues = {q.name: q for q in queues}
        self._cb = None

    def subscribe(self, cb):
        self._cb = cb
        return lambda: setattr(self, "_cb", None)

    def emit(self, ev):
        if self._cb is not None:
            self._cb(ev)


def _q(name, agent="claude", parallel=2):
    return Queue(name=name, agent_profile=agent, max_parallel=parallel)


def test_digest_initial_snapshot_has_zero_counts():
    sm = _StubManager([_q("tasks", "gemini-flash", 2)])
    d = QueueDigest(sm)
    d.start()

    snap = d.snapshot()
    assert snap.queues == [QueueView(
        name="tasks", agent="gemini-flash", max_parallel=2,
        running=0, queued=0, ok=0, err=0,
    )]
    assert snap.tasks == []
    assert snap.last_started is None


def test_digest_tracks_enqueue_dispatch_complete():
    sm = _StubManager([_q("tasks", "gemini-flash", 2)])
    d = QueueDigest(sm)
    d.start()

    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks",
        payload="summarize TASKS.md", enqueued_by="agent:lucid"))
    s = d.snapshot()
    assert s.queues[0].queued == 1
    assert len(s.tasks) == 1
    assert s.tasks[0].state == "queued"
    assert s.tasks[0].payload_summary == "summarize TASKS.md"

    sm.emit(QueueDispatched(
        task_id="t1", queue="tasks",
        worker_handle="brisk-curie", agent_slug="gemini-flash"))
    sm.emit(QueueStarted(task_id="t1", queue="tasks"))
    s = d.snapshot()
    assert s.queues[0].queued == 0 and s.queues[0].running == 1
    assert s.tasks[0].state == "running"
    assert s.tasks[0].worker_handle == "brisk-curie"
    assert s.last_started is not None
    assert s.last_started.task_id == "t1"

    sm.emit(QueueCompleted(
        task_id="t1", queue="tasks", outcome="completed",
        result="done", error=None,
        completed_at="2026-05-21T12:00:00Z"))
    s = d.snapshot()
    assert s.queues[0].running == 0 and s.queues[0].ok == 1
    assert s.tasks[0].state == "ok"


def test_digest_err_and_interrupted_counts_in_err():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    for tid, outcome in (("t1", "failed"), ("t2", "interrupted")):
        sm.emit(QueueEnqueued(
            task_id=tid, queue="tasks", payload="p",
            enqueued_by="agent:c"))
        sm.emit(QueueDispatched(
            task_id=tid, queue="tasks",
            worker_handle=f"w-{tid}", agent_slug="claude"))
        sm.emit(QueueStarted(task_id=tid, queue="tasks"))
        sm.emit(QueueCompleted(
            task_id=tid, queue="tasks", outcome=outcome,
            result=None, error="boom",
            completed_at="2026-05-21T12:00:00Z"))
    s = d.snapshot()
    assert s.queues[0].err == 2


def test_digest_recent_keeps_last_n_in_reverse_time_order():
    sm = _StubManager([_q("tasks", parallel=10)])
    d = QueueDigest(sm)
    d.start()
    for i in range(15):
        sm.emit(QueueEnqueued(
            task_id=f"t{i}", queue="tasks", payload=f"p{i}",
            enqueued_by="agent:c"))
        sm.emit(QueueDispatched(
            task_id=f"t{i}", queue="tasks",
            worker_handle=f"w{i}", agent_slug="claude"))
        sm.emit(QueueStarted(task_id=f"t{i}", queue="tasks"))
        sm.emit(QueueCompleted(
            task_id=f"t{i}", queue="tasks", outcome="completed",
            result="ok", error=None,
            completed_at=f"2026-05-21T12:00:{i:02d}Z"))
    s = d.snapshot()
    completed = [t for t in s.tasks if t.state in ("ok", "err")]
    assert len(completed) == 10  # default cap
    assert completed[0].task_id == "t14"  # newest first


def test_digest_unsubscribes_on_stop():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    d.stop()
    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks", payload="p",
        enqueued_by="agent:c"))
    assert d.snapshot().queues[0].queued == 0


def test_digest_records_assistant_text_per_worker():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks", payload="p",
        enqueued_by="agent:c"))
    sm.emit(QueueDispatched(
        task_id="t1", queue="tasks",
        worker_handle="brisk-curie", agent_slug="claude"))
    sm.emit(QueueStarted(task_id="t1", queue="tasks"))
    d.record_assistant_text("brisk-curie", "Reading TASKS.md…")
    d.record_assistant_text("brisk-curie", "Found 4 sections.")
    assert d.tail_of("t1") == [
        "Reading TASKS.md…", "Found 4 sections."]
