"""The session id is latched at first sight, not at the end of a turn.

A worker whose harness dies before any turn boundary never reaches
_finalize, and that is precisely the case the record exists for. So the
queue's event observer writes it the moment SystemInit carries one.
"""
from __future__ import annotations

from aegis.queue.jsonl import read_records
from tests.conftest import worker_handle


def _worker_sessions(tmp_path):
    recs = read_records(tmp_path / "queues" / "impl.jsonl")
    return [r for r in recs if r["event"] == "worker_session"]


async def test_worker_session_is_recorded_when_the_id_first_appears(
    queue_rig, tmp_path,
):
    qm, sm = queue_rig
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    sm.emit_system_init(worker_handle(qm, tid), session_id="sess-1")

    ws = _worker_sessions(tmp_path)
    assert len(ws) == 1
    assert ws[0]["task_id"] == tid
    assert ws[0]["session_id"] == "sess-1"
    assert ws[0]["agent_profile"] == "claude-impl"


async def test_worker_session_is_recorded_once(queue_rig, tmp_path):
    """A second SystemInit (a rebuild reports one too) must not append a
    duplicate that replay would have to de-duplicate."""
    qm, sm = queue_rig
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    h = worker_handle(qm, tid)
    sm.emit_system_init(h, session_id="sess-1")
    sm.emit_system_init(h, session_id="sess-1")

    assert len(_worker_sessions(tmp_path)) == 1


async def test_the_task_carries_the_resumable_after_the_record(queue_rig):
    """The log is for a human and for replay; the in-memory task is what
    the finalizer reads when it decides whether a rebuild is possible."""
    qm, sm = queue_rig
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    h = worker_handle(qm, tid)
    assert qm._all[tid].resumable is None
    sm.emit_system_init(h, session_id="sess-1")

    r = qm._all[tid].resumable
    assert r is not None and r.session_id == "sess-1"
    # _workers and _inflight hold their own copies of a frozen Task; a
    # record that updated only _all would leave the finalizer reading a
    # task with no resumable on it.
    assert qm._workers[h][0].resumable == r
    assert [t.resumable for t in qm._inflight["impl"]] == [r]


async def test_worker_session_is_not_a_lifecycle_event():
    """Membership in _LIFECYCLE_EVENTS sets the task's status to the event
    name on replay. A status of 'worker_session' matches no branch, which
    drops the task from _all with no callback and blocks its producer
    forever — the exact failure the comment in start() was written about."""
    from aegis.queue.manager import _LIFECYCLE_EVENTS
    assert "worker_session" not in _LIFECYCLE_EVENTS


async def test_a_late_record_does_not_resurrect_a_terminal_task(
    queue_rig, tmp_path,
):
    """Review Focus 3. The observer fires off a session the manager has
    already finalized and popped; it must find nothing and do nothing.

    Called DIRECTLY, not through `sm.emit_system_init`. Going through
    `AgentSession._fire_event` puts the guard behind a bare `except`: delete
    the `if handle not in self._workers: return` line and the body raises
    `KeyError`, `_fire_event` swallows it, and both assertions below still
    pass — so the test could not fail. The swallow has to be out of the path
    for the guard to be what is under test.
    """
    qm, sm = queue_rig
    tid, _ = qm.enqueue("impl", "go", enqueued_by="agent:producer",
                        callback=True)
    h = worker_handle(qm, tid)
    await sm.finish(h, text="DONE")
    assert qm.status(tid)["status"] == "completed"

    session = sm._session_for(h)
    session._session.session_id = "sess-late"
    qm._record_worker_session(h, session)

    assert qm.status(tid)["status"] == "completed"
    assert not [r for r in _worker_sessions(tmp_path)
                if r.get("session_id") == "sess-late"]
