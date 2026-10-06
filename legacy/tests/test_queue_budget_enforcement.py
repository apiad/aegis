import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

from tests.test_queue_manager import StubSessionManager


def _seed_completed(state_dir: Path, queue: str, usd: str,
                    minutes_ago: int = 5) -> None:
    log = state_dir / "queues" / f"{queue}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rec = {"event": "completed",
           "completed_at": (now - timedelta(minutes=minutes_ago)
                              ).isoformat().replace("+00:00", "Z"),
           "cost": {"usd": usd, "input_tokens": 0, "output_tokens": 0,
                     "cache_hit_tokens": 0, "cache_write_tokens": 0,
                     "thinking_tokens": 0}}
    log.write_text(json.dumps(rec) + "\n")


def _q_with_budget(usd: str = "1.00", window: str = "1h") -> Queue:
    return Queue(name="impl", agent_profile="opus", max_parallel=1,
                 provider="claude-code", model="opus",
                 budgets=[Budget("usd", Decimal(usd), window,
                                  parse_window(window))])


@pytest.mark.asyncio
async def test_enqueue_admits_when_budget_allows(tmp_path):
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q_with_budget()}, sm, inbox,
                       state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, tuple)
    tid, pos = result
    assert isinstance(tid, str)


@pytest.mark.asyncio
async def test_enqueue_rejects_when_budget_exhausted(tmp_path):
    _seed_completed(tmp_path, "impl", usd="1.50", minutes_ago=5)
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q_with_budget()}, sm, inbox,
                       state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, dict)
    assert "error" in result
    assert result["queue"] == "impl"
    assert len(result["blocked_by"]) == 1
    bc = result["blocked_by"][0]
    assert bc["constraint"] == "usd"
    assert Decimal(bc["spent"]) == Decimal("1.50")
    assert bc["window"] == "1h"
    assert bc["unblock_at"]
    assert result["unblock_at"]


@pytest.mark.asyncio
async def test_enqueue_no_budgets_unchanged(tmp_path):
    """Queue with no budgets: same tuple return as pre-v0.9."""
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus")
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, tuple)


@pytest.mark.asyncio
async def test_multi_budget_partial_block_names_only_blocking(tmp_path):
    _seed_completed(tmp_path, "impl", usd="1.50", minutes_ago=5)
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[
                  Budget("usd", Decimal("1.00"), "1h", parse_window("1h")),
                  Budget("usd", Decimal("10.00"), "24h", parse_window("24h")),
              ])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, dict)
    assert len(result["blocked_by"]) == 1
    assert result["blocked_by"][0]["window"] == "1h"
