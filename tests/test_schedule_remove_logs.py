import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane
from aegis.scheduler.push import write_atomic
from aegis.scheduler.scheduler import Scheduler


def _registry(known: set[str]):
    return SimpleNamespace(get=lambda name: object() if name in known else None)


async def _noop_run(name: str, args: dict):
    return None


def _build_scheduler(state_root, schedules):
    state_dir = state_root / ".aegis" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return Scheduler(
        schedules=schedules, state_dir=state_dir,
        run_workflow=_noop_run)


def _make_bridge(state_root, *, schedules, inline_names):
    sched = _build_scheduler(state_root, schedules)
    return SimpleNamespace(
        queue_manager=object(),
        inbox_router=None,
        state_root=state_root,
        workflow_registry=_registry({"enqueue"}),
        scheduler=sched,
        inline_schedule_names=lambda: set(inline_names),
    )


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_delete_pushed_schedule_removes_file(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    dest = write_atomic(tmp_path, "psh", spec, "peer:zion")
    bridge = _make_bridge(
        tmp_path, schedules={"psh": spec}, inline_names=set())
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.delete("/remote/v1/schedule/psh")
    assert r.status_code == 204, r.text
    assert not dest.exists()

    # Subsequent GET on a name with no file + no scheduler entry → still 200
    # (entry remains in scheduler memory, but file is gone). The plan only
    # asserts DELETE removes the file. So we re-build a fresh bridge with
    # no schedule registered to mirror a hot-reload picking up the deletion.
    fresh = _make_bridge(tmp_path, schedules={}, inline_names=set())
    app2 = build_plane(fresh, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app2) as c:
        r = await c.get("/remote/v1/schedule/psh")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_overlay_returns_409(tmp_path):
    p = tmp_path / ".aegis" / "schedules" / "ov.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "workflow: enqueue\ncron: \"0 2 * * *\"\nargs: {queue: impl, payload: x}\n")
    spec = {"workflow": "enqueue", "cron": "0 2 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    bridge = _make_bridge(
        tmp_path, schedules={"ov": spec}, inline_names=set())
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.delete("/remote/v1/schedule/ov")
    assert r.status_code == 409, r.text
    assert "overlay" in r.json()["error"]
    assert p.exists()


@pytest.mark.asyncio
async def test_delete_inline_returns_409(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 1 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    bridge = _make_bridge(
        tmp_path, schedules={"inl": spec}, inline_names={"inl"})
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.delete("/remote/v1/schedule/inl")
    assert r.status_code == 409, r.text
    assert "inline" in r.json()["error"]


@pytest.mark.asyncio
async def test_logs_returns_tail(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    write_atomic(tmp_path, "psh", spec, "peer:zion")
    bridge = _make_bridge(
        tmp_path, schedules={"psh": spec}, inline_names=set())

    log_path = tmp_path / ".aegis" / "state" / "schedules" / "psh.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = [{"event": "fire_started", "i": i} for i in range(5)]
    log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.get("/remote/v1/schedule/psh/logs?tail=3")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["records"] == records[-3:]
