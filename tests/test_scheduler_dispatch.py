"""Tests for scheduler tick + dispatch."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aegis.scheduler.clock import FakeClock
from aegis.scheduler.scheduler import Scheduler


def _entry(**kw) -> dict:
    base = {
        "workflow": "prompt",
        "args": {"agent": "c", "text": "hi"},
        "cron": "0 2 * * *",
        "timezone": "UTC",
        "lifecycle": "forever",
        "on_overlap": "skip",
        "enabled": True,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_tick_dispatches_eligible_schedule(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []

    async def fake_run(name: str, args: dict) -> str:
        calls.append((name, args))
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()},
        state_dir=tmp_path,
        run_workflow=fake_run,
        clock=clock,
    )
    # next_fire is 02:00; now is 01:59 — not yet eligible.
    await sched.tick()
    await asyncio.sleep(0.02)
    assert calls == []

    # Advance past the fire time.
    clock.advance(minutes=2)  # now 02:01
    await sched.tick()
    await asyncio.sleep(0.05)
    assert calls == [("prompt", {"agent": "c", "text": "hi"})]


@pytest.mark.asyncio
async def test_disabled_schedule_does_not_fire(tmp_path: Path) -> None:
    calls = []

    async def fake_run(name, args):
        calls.append(name)
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 3, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry(enabled=False)},
        state_dir=tmp_path,
        run_workflow=fake_run,
        clock=clock,
    )
    await sched.tick()
    await asyncio.sleep(0.02)
    assert calls == []


@pytest.mark.asyncio
async def test_on_overlap_skip_drops_concurrent(tmp_path: Path) -> None:
    """Second tick while first is in-flight is skipped."""
    started = 0
    gate = asyncio.Event()

    async def slow_run(name, args):
        nonlocal started
        started += 1
        await gate.wait()
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, 30, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry(cron="* * * * *", on_overlap="skip")},
        state_dir=tmp_path,
        run_workflow=slow_run,
        clock=clock,
    )
    clock.advance(seconds=45)  # now 2:00:15 — past first fire
    await sched.tick()
    await asyncio.sleep(0.02)
    assert started == 1

    clock.advance(minutes=1)
    await sched.tick()
    await asyncio.sleep(0.02)
    assert started == 1  # second fire skipped — first still in-flight

    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_fire_logs_jsonl(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()},
        state_dir=tmp_path,
        run_workflow=fake_run,
        clock=clock,
    )
    clock.advance(minutes=2)  # now 02:01 — past fire time
    await sched.tick()
    await asyncio.sleep(0.05)
    log = tmp_path / "schedules" / "eod.jsonl"
    lines = log.read_text().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert "fire_requested" in events
    assert "fire_completed" in events


@pytest.mark.asyncio
async def test_workflow_exception_recorded_as_failed_crash(tmp_path: Path) -> None:
    async def bad_run(name, args):
        raise ValueError("boom")

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()},
        state_dir=tmp_path,
        run_workflow=bad_run,
        clock=clock,
    )
    clock.advance(minutes=2)  # now 02:01 — past fire time
    await sched.tick()
    await asyncio.sleep(0.05)
    log = tmp_path / "schedules" / "eod.jsonl"
    lines = log.read_text().splitlines()
    last = json.loads(lines[-1])
    assert last["event"] == "fire_failed"
    assert last["status"] == "failed:crash"


@pytest.mark.asyncio
async def test_snapshot_written_after_tick(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()},
        state_dir=tmp_path,
        run_workflow=fake_run,
        clock=clock,
    )
    await sched.tick()
    snap_path = tmp_path / "schedules.snapshot.json"
    assert snap_path.exists()
    snap = json.loads(snap_path.read_text())
    assert "eod" in snap
    assert "next_fire" in snap["eod"]


@pytest.mark.asyncio
async def test_fire_at_one_shot(tmp_path: Path) -> None:
    """A schedule with fire_at + lifecycle=once fires once; subsequent
    ticks do not re-fire."""
    calls: list[str] = []

    async def fake_run(name, args):
        calls.append(name)
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 13, 59, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"oneoff": {
            "workflow": "prompt", "args": {"agent": "c", "text": "x"},
            "fire_at": "2026-05-25T14:00:00+00:00",
            "lifecycle": "once", "on_overlap": "skip",
            "timeout": 60, "enabled": True,
        }},
        state_dir=tmp_path, run_workflow=fake_run, clock=clock,
    )
    clock.advance(minutes=2)  # past 14:00
    await sched.tick()
    await asyncio.sleep(0.05)
    assert len(calls) == 1

    clock.advance(hours=1)
    await sched.tick()
    await asyncio.sleep(0.05)
    assert len(calls) == 1  # lifecycle:once exhausted


@pytest.mark.asyncio
async def test_start_stop_lifecycle(tmp_path: Path) -> None:
    """Start + immediate stop. Tick loop must not deadlock."""
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={},
        state_dir=tmp_path,
        run_workflow=fake_run,
        clock=clock,
        cfg=__import__("aegis.scheduler",
                        fromlist=["SchedulerConfig"]).SchedulerConfig(
            tick_seconds=1),
    )
    await sched.start()
    await asyncio.sleep(0.1)
    await sched.stop()
