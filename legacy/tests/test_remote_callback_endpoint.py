import httpx
import pytest
from dataclasses import dataclass
from httpx import ASGITransport

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


class _FakeInboxRouter:
    def __init__(self):
        self.deliveries = []
    async def deliver(self, handle, msg):
        self.deliveries.append({"handle": handle, "msg": msg})


class _FakeQueueManager:
    """Minimal stand-in; /callback doesn't actually need the QM but the
    plane still wires both fields onto the bridge."""
    def enqueue(self, *a, **k):
        return ("ignored", 0)


@dataclass
class _Bridge:
    queue_manager: object
    inbox_router: object


@pytest.mark.asyncio
async def test_callback_endpoint_routes_to_inbox():
    inbox = _FakeInboxRouter()
    bridge = _Bridge(queue_manager=_FakeQueueManager(), inbox_router=inbox)
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(bridge, spec)
    transport = ASGITransport(app=app)
    body = {
        "task_id": "01J", "queue": "impl",
        "from_peer": "vps", "to_handle": "lucid-knuth",
        "status": "ok", "result_text": "DONE",
        "started_at": "", "ended_at": "",
    }
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/callback", json=body)
        assert r.status_code == 204
    assert len(inbox.deliveries) == 1
    d = inbox.deliveries[0]
    assert d["handle"] == "lucid-knuth"
    msg = d["msg"]
    assert msg.body == "DONE"
    assert "queue:vps:impl" in msg.sender
    assert msg.task_id == "01J"
    assert msg.status == "ok"


@pytest.mark.asyncio
async def test_callback_endpoint_auth_rejects_bad_token():
    bridge = _Bridge(queue_manager=_FakeQueueManager(),
                     inbox_router=_FakeInboxRouter())
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(bridge, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/callback", json={
            "task_id": "x", "queue": "q", "from_peer": "p",
            "to_handle": "h", "status": "ok", "result_text": "",
            "started_at": "", "ended_at": ""},
            headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_callback_endpoint_auth_accepts_good_token():
    inbox = _FakeInboxRouter()
    bridge = _Bridge(queue_manager=_FakeQueueManager(), inbox_router=inbox)
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(bridge, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                  base_url="http://test") as client:
        r = await client.post("/remote/v1/callback", json={
            "task_id": "x", "queue": "q", "from_peer": "p",
            "to_handle": "h", "status": "ok", "result_text": "ok",
            "started_at": "", "ended_at": ""},
            headers={"Authorization": "Bearer good"})
        assert r.status_code == 204
    assert len(inbox.deliveries) == 1
