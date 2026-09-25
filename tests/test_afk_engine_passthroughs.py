"""The three passthroughs the AFK coordinator needs from aegis core."""
from __future__ import annotations

from unittest.mock import MagicMock

from aegis.workflow.engine import WorkflowEngine


def _engine(*, queue=None, bridge=None) -> WorkflowEngine:
    return WorkflowEngine(
        name="afk",
        workflow_id="wf-1",
        bridge=bridge or MagicMock(),
        queue_manager=queue or MagicMock(),
        inbox_router=MagicMock(),
    )


def test_task_status_passes_through() -> None:
    queue = MagicMock()
    queue.status.return_value = {"status": "completed", "result": "done"}
    assert _engine(queue=queue).task_status("t-1") == {
        "status": "completed",
        "result": "done",
    }
    queue.status.assert_called_once_with("t-1")


def test_task_status_unknown_task_is_none() -> None:
    queue = MagicMock()
    queue.status.return_value = None
    assert _engine(queue=queue).task_status("nope") is None


def test_plan_state_passes_through() -> None:
    bridge = MagicMock()
    sentinel = object()
    bridge.plan_state.return_value = sentinel
    assert _engine(bridge=bridge).plan_state("worker-1") is sentinel
    bridge.plan_state.assert_called_once_with("worker-1")


def test_queue_status_carries_worker_handle(tmp_path) -> None:
    """The handle is already on the Task record; status must surface it,
    because it is the only link from a card's task_id to the session whose
    plan the progress tick reads."""
    from aegis.queue.manager import QueueManager

    qm = QueueManager({}, MagicMock(), MagicMock(), state_dir=tmp_path)
    task = MagicMock()
    task.id = "t-9"
    task.status = "running"
    task.result = None
    task.error = None
    task.completed_at = None
    task.worker_handle = "afk-worker-3"
    qm._all["t-9"] = task

    out = qm.status("t-9")
    assert out["worker_handle"] == "afk-worker-3"
