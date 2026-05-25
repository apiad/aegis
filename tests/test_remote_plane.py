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
