from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from httpx import ASGITransport

from aegis.remote.client import remote_enqueue
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane


@dataclass
class _FakeQM:
    raise_on: str | None = None

    def enqueue(self, queue, payload, *, enqueued_by, callback):
        if self.raise_on and queue == self.raise_on:
            raise KeyError(queue)
        return ("tid-01J", 0)


@pytest.mark.asyncio
async def test_remote_enqueue_happy_path(monkeypatch) -> None:
    qm = _FakeQM()
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test")

    transport = ASGITransport(app=app)

    async def _client_factory(_: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=spec.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "implementation", "do it", "zion")
    assert result == {
        "task_id": "tid-01J",
        "queued_position": 0,
        "target_url": "http://test",
    }


@pytest.mark.asyncio
async def test_remote_enqueue_unknown_queue_returns_error(monkeypatch) -> None:
    qm = _FakeQM(raise_on="nope")
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:8556")
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test")

    transport = ASGITransport(app=app)

    async def _client_factory(_: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=spec.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "nope", "x", "zion")
    assert "error" in result
    assert "unknown queue" in result["error"]


@pytest.mark.asyncio
async def test_remote_enqueue_connection_refused(monkeypatch) -> None:
    spec = RemoteSpec(url="http://127.0.0.1:1")  # nothing listens here

    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=s.url, timeout=httpx.Timeout(
            connect=1.0, read=1.0, write=1.0, pool=1.0))
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "q", "p", "zion")
    assert "error" in result
    assert "unreachable" in result["error"] or "refused" in result["error"]


@pytest.mark.asyncio
async def test_remote_enqueue_sends_bearer_token(monkeypatch) -> None:
    qm = _FakeQM()
    plane_spec = RemotePlaneSpec(bind="127.0.0.1:8556", accept_tokens=["good"])
    app = build_plane(qm, plane_spec)
    spec = RemoteSpec(url="http://test", token="good")

    transport = ASGITransport(app=app)

    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        headers = {}
        if s.token:
            headers["Authorization"] = f"Bearer {s.token}"
        return httpx.AsyncClient(
            transport=transport, base_url=s.url, headers=headers)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    result = await remote_enqueue(spec, "q", "p", "zion")
    assert "task_id" in result
