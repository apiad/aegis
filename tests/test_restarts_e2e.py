"""Restarts end to end: the real app under uvicorn, the fake claude, restarted
the way a person or systemd restarts it."""

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
