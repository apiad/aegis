from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


@dataclass
class _FakeQueueManager:
    """Records enqueue calls for assertion."""
    calls: list[dict[str, Any]]

    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool) -> tuple[str, int]:
        self.calls.append({
            "queue": queue,
            "payload": payload,
            "enqueued_by": enqueued_by,
            "callback": callback,
        })
        return ("task-01J", 0)


@pytest.mark.asyncio
async def test_enqueue_happy_path() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, spec)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "implementation",
                  "payload": "do the thing",
                  "from": "zion"})

    assert resp.status_code == 200
    assert resp.json() == {"task_id": "task-01J", "queued_position": 0}
    assert qm.calls == [{
        "queue": "implementation",
        "payload": "do the thing",
        "enqueued_by": "remote:zion",
        "callback": False,
    }]


@pytest.mark.asyncio
async def test_enqueue_unknown_queue_returns_404() -> None:
    class _Raising:
        def enqueue(self, *a, **k):
            raise KeyError("nope")
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(_Raising(), spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "nope", "payload": "x", "from": "zion"})
    assert resp.status_code == 404
    assert "unknown queue" in resp.json()["error"]


@pytest.mark.asyncio
async def test_enqueue_bad_body_returns_400() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue", json={"queue": "x"})  # missing fields
    assert resp.status_code == 400
    assert "missing" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_token_required_when_configured() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_accepted_when_matching() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            headers={"Authorization": "Bearer good"},
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ip_allowlist_rejects_unlisted() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)
    transport = ASGITransport(
        app=app, client=("192.168.1.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ip_allowlist_accepts_listed() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)
    transport = ASGITransport(app=app, client=("10.0.0.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_both_gates_must_pass() -> None:
    qm = _FakeQueueManager(calls=[])
    spec = RemotePlaneSpec(
        bind="127.0.0.1:8556",
        accept_tokens=["good"],
        accept_from=["10.0.0.1"])
    app = build_plane(qm, spec)

    # Right IP, wrong token: 401
    transport = ASGITransport(app=app, client=("10.0.0.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            json={"queue": "q", "payload": "p", "from": "zion"})
        assert resp.status_code == 401

    # Wrong IP, right token: 403
    transport = ASGITransport(app=app, client=("192.168.1.1", 12345))
    async with httpx.AsyncClient(
            transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/remote/v1/enqueue",
            headers={"Authorization": "Bearer good"},
            json={"queue": "q", "payload": "p", "from": "zion"})
        assert resp.status_code == 403
