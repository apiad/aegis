from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.mcp.server import build_server
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.remote.config import RemoteSpec

from tests.test_queue_manager import StubSessionManager


class _Bridge:
    def __init__(self, qm, remotes=None):
        self.queue_manager = qm
        self.inbox_router = qm._inbox
        self.remotes = remotes or {}
        self.remote_plane = None


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


@pytest.mark.asyncio
async def test_budget_status_local_no_queue_lists_all(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
        "fast": Queue(name="fast", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus"),
    }, sm, inbox, state_dir=tmp_path)
    server = build_server(_Bridge(qm))
    r = await _call(server, "aegis_budget_status", from_handle="h")
    assert "queues" in r


@pytest.mark.asyncio
async def test_budget_status_local_with_queue(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
    }, sm, inbox, state_dir=tmp_path)
    server = build_server(_Bridge(qm))
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", queue="impl")
    assert r["name"] == "impl"
    assert "checks" in r


@pytest.mark.asyncio
async def test_budget_status_remote_routes_through_client(monkeypatch, tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    bridge = _Bridge(qm, remotes={"vps": RemoteSpec(url="http://x")})
    captured = {}
    async def fake_list(spec):
        captured["called"] = True
        return {"queues": []}
    monkeypatch.setattr("aegis.remote.client.remote_budget_list", fake_list)
    server = build_server(bridge)
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", target="vps")
    assert captured.get("called")


@pytest.mark.asyncio
async def test_budget_status_unknown_target_errors(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    bridge = _Bridge(qm, remotes={})
    server = build_server(bridge)
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", target="vps")
    assert "error" in r
