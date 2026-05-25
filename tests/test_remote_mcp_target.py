from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import build_server
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane


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


@dataclass
class _FakeQM:
    enqueue_calls: list[Any] = field(default_factory=list)

    def enqueue(self, queue, payload, *, enqueued_by, callback):
        self.enqueue_calls.append((queue, payload, enqueued_by, callback))
        return ("local-tid", 0)


class _FakeBridge:
    def __init__(self, qm, remotes):
        from aegis.queue import InboxRouter
        self.queue_manager = qm
        self.remotes = remotes
        self.inbox_router = InboxRouter()
        self.canvas_manager = None
        self.terminal_manager = None
        self.groups = None

    def list_sessions(self) -> list[SessionInfo]:
        return []

    def list_agents(self) -> list[str]:
        return []

    async def handoff(self, *a, **k) -> str:
        return ""

    async def spawn(self, *a, **k) -> str:
        return ""

    async def close(self, *a, **k) -> None:
        return None


@pytest.mark.asyncio
async def test_aegis_enqueue_local_path_unchanged() -> None:
    qm = _FakeQM()
    bridge = _FakeBridge(qm=qm, remotes={})
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="q", payload="p", from_handle="h")
    assert result["task_id"] == "local-tid"
    from aegis.queue import sender_agent
    assert qm.enqueue_calls == [("q", "p", sender_agent("h"), True)]


@pytest.mark.asyncio
async def test_aegis_enqueue_unknown_target_errors() -> None:
    qm = _FakeQM()
    bridge = _FakeBridge(qm=qm, remotes={})
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="q", payload="p",
                         from_handle="h", target="vps")
    assert "error" in result
    assert "unknown target" in result["error"]
    assert qm.enqueue_calls == []


@pytest.mark.asyncio
async def test_aegis_enqueue_with_target_routes_remote(monkeypatch) -> None:
    remote_qm = _FakeQM()
    plane_app = build_plane(remote_qm, RemotePlaneSpec(bind="127.0.0.1:8556"))

    transport = ASGITransport(app=plane_app)

    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=s.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    local_qm = _FakeQM()
    bridge = _FakeBridge(
        qm=local_qm,
        remotes={"vps": RemoteSpec(url="http://stub")})
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="implementation", payload="build it",
                         from_handle="h", target="vps")

    assert result["task_id"] == "local-tid"
    assert local_qm.enqueue_calls == []
    assert len(remote_qm.enqueue_calls) == 1
    q, p, eb, cb = remote_qm.enqueue_calls[0]
    assert (q, p, eb, cb) == ("implementation", "build it", "remote:h", False)
