import asyncio
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


def _make_overlay_file(state_root, name, body_yaml):
    p = state_root / ".aegis" / "schedules" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_yaml)
    return p


@pytest.mark.asyncio
async def test_list_classifies_sources(tmp_path):
    inline_spec = {"workflow": "enqueue", "cron": "0 1 * * *",
                   "args": {"queue": "impl", "payload": "x"}}
    overlay_spec = {"workflow": "enqueue", "cron": "0 2 * * *",
                    "args": {"queue": "impl", "payload": "x"}}
    pushed_spec = {"workflow": "enqueue", "cron": "0 3 * * *",
                   "args": {"queue": "impl", "payload": "x"}}

    # Overlay: file present without provenance comment.
    _make_overlay_file(
        tmp_path, "ov",
        "workflow: enqueue\ncron: \"0 2 * * *\"\nargs: {queue: impl, payload: x}\n")
    # Pushed: file with provenance header written via write_atomic.
    write_atomic(tmp_path, "psh", pushed_spec, "peer:zion")

    schedules = {"inl": inline_spec, "ov": overlay_spec, "psh": pushed_spec}
    bridge = _make_bridge(
        tmp_path, schedules=schedules, inline_names={"inl"})

    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.get("/remote/v1/schedule")
    assert r.status_code == 200, r.text
    rows = {row["name"]: row for row in r.json()["schedules"]}
    assert rows["inl"]["source"] == "inline"
    assert rows["ov"]["source"] == "overlay"
    assert rows["psh"]["source"] == "pushed"
    assert rows["psh"]["workflow"] == "enqueue"
    assert rows["psh"]["cron"] == "0 3 * * *"


@pytest.mark.asyncio
async def test_show_returns_full_spec_and_runtime(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    write_atomic(tmp_path, "psh", spec, "peer:zion")
    bridge = _make_bridge(
        tmp_path, schedules={"psh": spec}, inline_names=set())
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.get("/remote/v1/schedule/psh")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "psh"
    assert data["source"] == "pushed"
    assert data["spec"]["workflow"] == "enqueue"
    assert data["spec"]["cron"] == "0 3 * * *"
    rt = data["runtime"]
    assert "next_fire" in rt and rt["next_fire"] is not None
    assert "last_fire" in rt
    assert rt["fire_count"] == 0
    assert rt["in_flight"] is False
    assert rt["enabled"] is True
    assert data["pushed_from"] == "peer:zion"
    assert data["pushed_at"]


@pytest.mark.asyncio
async def test_show_404_for_missing(tmp_path):
    bridge = _make_bridge(
        tmp_path, schedules={}, inline_names=set())
    app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
    async with _client(app) as c:
        r = await c.get("/remote/v1/schedule/nope")
    assert r.status_code == 404
    assert r.json() == {"error": "not found"}
