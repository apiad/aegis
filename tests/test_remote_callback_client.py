from __future__ import annotations

import httpx
import pytest

from aegis.remote.client import remote_callback
from aegis.remote.config import RemoteSpec


@pytest.mark.asyncio
async def test_remote_callback_posts_body_and_token(monkeypatch) -> None:
    captured: list[httpx.Request] = []

    async def _fake_client(spec: RemoteSpec) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if spec.token:
            headers["Authorization"] = f"Bearer {spec.token}"

        async def _transport_handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(204)

        transport = httpx.MockTransport(_transport_handler)
        return httpx.AsyncClient(
            base_url=spec.url, headers=headers, transport=transport)

    monkeypatch.setattr("aegis.remote.client._build_client", _fake_client)

    spec = RemoteSpec(url="http://1.2.3.4:8556", token="secret")
    body = {
        "task_id": "01J123", "queue": "impl", "from_peer": "vps",
        "to_handle": "lucid-knuth", "status": "ok", "result_text": "done",
        "started_at": "2026-05-25T10:00:00Z",
        "ended_at": "2026-05-25T10:05:00Z",
    }
    result = await remote_callback(spec, body)
    assert result == {"ok": True}
    assert len(captured) == 1
    assert captured[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_remote_callback_normalizes_5xx(monkeypatch) -> None:
    async def _fake_client(spec: RemoteSpec) -> httpx.AsyncClient:
        async def _transport_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        transport = httpx.MockTransport(_transport_handler)
        return httpx.AsyncClient(base_url=spec.url, transport=transport)

    monkeypatch.setattr("aegis.remote.client._build_client", _fake_client)

    spec = RemoteSpec(url="http://1.2.3.4:8556")
    result = await remote_callback(spec, {
        "task_id": "x", "queue": "q",
        "from_peer": "p", "to_handle": "h",
        "status": "ok", "result_text": "",
        "started_at": "", "ended_at": "",
    })
    assert result.get("error", "").startswith("callback_dropped:")


@pytest.mark.asyncio
async def test_callback_observer_fires_on_completion(tmp_path, monkeypatch) -> None:
    """End-to-end: when a task with callback_to completes, the observer
    POSTs /remote/v1/callback to the caller's plane."""
    import asyncio
    import json

    from aegis.queue import InboxRouter
    from aegis.queue.manager import QueueManager
    from aegis.remote.callback_observer import install_callback_observer

    from tests.test_queue_manager import StubSessionManager, _q

    # Track outgoing callback POST bodies.
    posted_bodies: list[dict] = []

    async def _fake_client(spec: RemoteSpec) -> httpx.AsyncClient:
        async def _transport_handler(request: httpx.Request) -> httpx.Response:
            posted_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        transport = httpx.MockTransport(_transport_handler)
        return httpx.AsyncClient(base_url=spec.url, transport=transport)

    monkeypatch.setattr("aegis.remote.client._build_client", _fake_client)

    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    remotes = {"zion": RemoteSpec(url="http://1.2.3.4:8556")}
    install_callback_observer(qm, remotes=remotes, self_peer_name="vps")

    tid, _ = qm.enqueue("impl", "do it",
                        enqueued_by="remote:zion",
                        callback_to="zion",
                        callback_handle="lucid-knuth")
    # The StubSessionManager's default script yields AssistantText("DONE") +
    # Result(...), so the task auto-completes via the existing event flow.
    # Wait for completion + the fire-and-forget callback task to run.
    for _ in range(50):
        await asyncio.sleep(0.01)
        task = qm._all[tid]
        if task.status == "completed":
            break
    # Give the fire-and-forget callback task a tick.
    await asyncio.sleep(0.05)

    assert len(posted_bodies) == 1, "callback POST was never sent"
    cb = posted_bodies[0]
    assert cb["to_handle"] == "lucid-knuth"
    assert "DONE" in cb["result_text"]
    assert cb["from_peer"] == "vps"
    assert cb["status"] == "ok"
