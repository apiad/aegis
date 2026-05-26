import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget import BudgetExceeded
from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.workflow.engine import WorkflowEngine

from tests.test_queue_manager import StubSessionManager


class _StubBridge:
    queue_manager = None
    inbox_router = None

    def list_sessions(self):
        return []

    def list_agents(self):
        return ["default"]


@pytest.mark.asyncio
async def test_engine_enqueue_raises_on_budget_exhausted(tmp_path):
    log = tmp_path / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "completed",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[Budget("usd", Decimal("1.00"), "1h",
                              parse_window("1h"))])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    engine = WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path)
    with pytest.raises(BudgetExceeded) as ei:
        await engine.enqueue("impl", "x")
    assert ei.value.queue == "impl"
    assert ei.value.decision.allowed is False
    assert "1.50" in str(ei.value)


@pytest.mark.asyncio
async def test_engine_enqueue_under_budget_returns_task_id(tmp_path):
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[Budget("usd", Decimal("1.00"), "1h",
                              parse_window("1h"))])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    engine = WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path)
    tid = await engine.enqueue("impl", "x")
    assert isinstance(tid, str)


@pytest.mark.asyncio
async def test_engine_delegate_raises_on_budget_exhausted(tmp_path):
    log = tmp_path / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "completed",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[Budget("usd", Decimal("1.00"), "1h",
                              parse_window("1h"))])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    engine = WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path)
    with pytest.raises(BudgetExceeded):
        await engine.delegate("impl", "x")
