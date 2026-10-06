import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane

from tests.test_queue_manager import StubSessionManager


def _bridge(qm):
    """Minimal bridge for the plane: needs queue_manager."""
    class B:
        queue_manager = qm
        inbox_router = qm._inbox
        remote_plane = None
        remotes = {}
    return B()


@pytest.mark.asyncio
async def test_budget_list_includes_all_queues(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
        "fast": Queue(name="fast", agent_profile="haiku", max_parallel=2,
                       provider="claude-code", model="haiku"),
    }, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget")
        assert r.status_code == 200
    data = r.json()
    names = {q["name"] for q in data["queues"]}
    assert names == {"impl", "fast"}
    fast = next(q for q in data["queues"] if q["name"] == "fast")
    assert fast["budgets_count"] == 0
    assert fast["status"] == "no-budget"


@pytest.mark.asyncio
async def test_budget_show_blocked(tmp_path):
    log = tmp_path / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "completed",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")

    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
    }, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget/impl")
        assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert len(data["blocked_by"]) == 1
    assert Decimal(data["blocked_by"][0]["spent"]) == Decimal("1.50")


@pytest.mark.asyncio
async def test_budget_show_unknown_queue_404(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget/ghost")
        assert r.status_code == 404
