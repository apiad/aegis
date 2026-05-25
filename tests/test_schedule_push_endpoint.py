import os
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane


def _registry(known: set[str]):
    return SimpleNamespace(get=lambda name: object() if name in known else None)


def _make_bridge(tmp_path, *, known=("enqueue",)):
    return SimpleNamespace(
        queue_manager=object(),
        inbox_router=None,
        state_root=tmp_path,
        workflow_registry=_registry(set(known)),
    )


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_push_writes_yaml_with_provenance(tmp_path):
    bridge = _make_bridge(tmp_path)
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {
        "workflow": "enqueue",
        "args": {"queue": "impl", "payload": "x", "callback": False},
        "cron": "0 2 * * *",
        "lifecycle": "forever",
    }
    async with _client(app) as c:
        r = await c.put("/remote/v1/schedule/nightly", json=body,
                        headers={"X-Pushed-From": "peer:zion"})
        assert r.status_code == 200, r.text
    written = tmp_path / ".aegis" / "schedules" / "nightly.yaml"
    assert written.exists()
    content = written.read_text()
    assert content.startswith("# pushed_from: peer:zion")
    assert 'cron: "0 2 * * *"' in content


@pytest.mark.asyncio
async def test_push_rejects_bad_cron(tmp_path):
    bridge = _make_bridge(tmp_path)
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {"workflow": "enqueue", "args": {"queue": "impl", "payload": "x"},
            "cron": "not a cron", "lifecycle": "forever"}
    async with _client(app) as c:
        r = await c.put("/remote/v1/schedule/n", json=body)
        assert r.status_code == 400
        assert "cron" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_push_rejects_unknown_workflow(tmp_path):
    bridge = _make_bridge(tmp_path, known=("enqueue",))
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {"workflow": "does_not_exist", "args": {},
            "cron": "0 2 * * *", "lifecycle": "forever"}
    async with _client(app) as c:
        r = await c.put("/remote/v1/schedule/n", json=body)
        assert r.status_code == 400
        assert "unknown workflow" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_push_rejects_callback_true_on_enqueue_workflow(tmp_path):
    bridge = _make_bridge(tmp_path)
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {
        "workflow": "enqueue",
        "args": {"queue": "impl", "payload": "x", "callback": True},
        "cron": "0 2 * * *",
        "lifecycle": "forever",
    }
    async with _client(app) as c:
        r = await c.put("/remote/v1/schedule/n", json=body)
        assert r.status_code == 400
        assert "callback" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_push_is_atomic(tmp_path):
    bridge = _make_bridge(tmp_path)
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    body = {"workflow": "enqueue",
            "args": {"queue": "impl", "payload": "x"},
            "cron": "0 2 * * *", "lifecycle": "forever"}
    async with _client(app) as c:
        r = await c.put("/remote/v1/schedule/atomic", json=body)
        assert r.status_code == 200
    sched_dir = tmp_path / ".aegis" / "schedules"
    assert (sched_dir / "atomic.yaml").exists()
    leftovers = [f for f in os.listdir(sched_dir) if f.endswith(".tmp")]
    assert leftovers == []
