"""Restarts end to end: the real app under uvicorn, the fake claude, restarted
the way a person or systemd restarts it."""

import asyncio
import json
import os
import signal

import pytest

from aegis import restarts

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def said(s, text):
    return any(text in (e.get("md") or "") for e in s.entries() if e["kind"] == "inbox")


async def mid_turn(s):
    await s.send("/sleep 30")
    await until(lambda: any(e["kind"] == "tool" for e in s.entries()), what="the call")


async def test_a_restart_continues_the_turn_it_cut(world):
    a = await world.spawn()
    await mid_turn(a)
    await world.restart()
    record = json.loads((world.app.roots.state_root / restarts.RECORD).read_text())
    assert set(record) == {"started_at"}
    a2 = world.session(a.log_id)
    await until(
        lambda: a2.status == "idle" and said(a2, restarts.RESTARTED),
        timeout=10,
        what="the continued turn",
    )


async def test_a_harness_killed_by_the_units_sigterm_still_continues(world):
    a = await world.spawn()
    await mid_turn(a)
    # KillMode=control-group: the harness dies first, then aegis stops.
    os.kill(a.pid, signal.SIGTERM)
    await until(lambda: a.status == "stopped", what="the SIGTERM exit")
    await world.restart()
    a2 = world.session(a.log_id)
    await until(
        lambda: a2.status == "idle" and said(a2, restarts.RESTARTED),
        timeout=10,
        what="the continued turn",
    )


async def test_a_worker_killed_by_sigterm_keeps_its_task_and_finishes_it(world):
    a = await world.spawn()
    out = await turn(a, mcp("queue_enqueue", queue="general", payload="/sleep 30"))
    task_id = json.loads(out.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(
        lambda: (
            t.worker
            and any(e["kind"] == "tool" for e in world.session(t.worker).entries())
        ),
        what="the worker's call",
    )
    w = world.session(t.worker)
    os.kill(w.pid, signal.SIGTERM)
    await until(lambda: w.status == "stopped", what="the SIGTERM exit")
    assert t.status == "running"
    await world.restart()
    await until(
        lambda: world.app.queues.tasks[task_id].status == "completed",
        timeout=12,
        what="the task",
    )


async def worker_mid_turn(world):
    a = await world.spawn()
    out = await turn(a, mcp("queue_enqueue", queue="general", payload="/sleep 30"))
    task_id = json.loads(out.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(
        lambda: (
            t.worker
            and any(e["kind"] == "tool" for e in world.session(t.worker).entries())
        ),
        what="the worker's call",
    )
    return task_id, world.session(t.worker)


async def test_a_worker_whose_continue_cannot_start_fails_its_task(world, tmp_path):
    task_id, _ = await worker_mid_turn(world)
    await world.stop()
    world.fake = str(tmp_path / "no-such-claude")
    await world.start()
    await until(
        lambda: world.app.queues.tasks[task_id].status == "failed",
        timeout=8,
        what="the task to fail",
    )


async def test_a_stray_sigterm_to_a_worker_fails_its_task_when_no_restart_follows(
    world, monkeypatch
):
    monkeypatch.setattr(restarts, "GRACE_S", 0.5)
    task_id, w = await worker_mid_turn(world)
    os.kill(w.pid, signal.SIGTERM)
    await until(
        lambda: world.app.queues.tasks[task_id].status == "failed",
        timeout=5,
        what="the task to fail",
    )
    assert "SIGTERM" in world.app.queues.tasks[task_id].error


async def test_a_grace_timer_from_before_a_restart_changes_nothing_after_it(
    world, monkeypatch
):
    monkeypatch.setattr(restarts, "GRACE_S", 1.0)
    task_id, w = await worker_mid_turn(world)
    os.kill(w.pid, signal.SIGTERM)
    await until(lambda: w.status == "stopped", what="the SIGTERM exit")
    await world.restart()
    await asyncio.sleep(1.5)  # past the old server's grace timer
    log = (world.app.roots.state_root / "tasks.jsonl").read_text()
    assert '"event": "failed"' not in log
    await until(
        lambda: world.app.queues.tasks[task_id].status == "completed",
        timeout=12,
        what="the task",
    )
