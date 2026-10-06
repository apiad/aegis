"""Tests for on_overlap policies: queue, kill."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aegis.scheduler.clock import FakeClock
from aegis.scheduler.scheduler import Scheduler


@pytest.mark.asyncio
async def test_on_overlap_queue_runs_after_first(tmp_path: Path) -> None:
    started: list[str] = []
    completed: list[str] = []
    gate = asyncio.Event()

    async def slow(name, args):
        started.append(name)
        await gate.wait()
        completed.append(name)
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, 30, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": {
            "workflow": "prompt", "args": {},
            "cron": "* * * * *", "timezone": "UTC",
            "lifecycle": "forever", "on_overlap": "queue",
            "timeout": 60, "enabled": True,
        }},
        state_dir=tmp_path, run_workflow=slow, clock=clock,
    )
    clock.advance(seconds=45)  # past 02:00:00
    await sched.tick()           # fire 1
    await asyncio.sleep(0.02)
    assert started == ["prompt"]

    clock.advance(minutes=1)
    await sched.tick()           # fire 2 — first still running; queued
    await asyncio.sleep(0.02)
    assert started == ["prompt"]  # not yet started — still in queue

    gate.set()
    # Wait for both to drain.
    for _ in range(50):
        if len(completed) == 2:
            break
        await asyncio.sleep(0.01)
    assert started == ["prompt", "prompt"]
    assert completed == ["prompt", "prompt"]


@pytest.mark.asyncio
async def test_on_overlap_kill_cancels_prior(tmp_path: Path) -> None:
    cancelled: list[str] = []
    started: list[str] = []

    async def slow(name, args):
        started.append(name)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 59, 30, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"x": {
            "workflow": "prompt", "args": {}, "cron": "* * * * *",
            "timezone": "UTC", "lifecycle": "forever",
            "on_overlap": "kill", "timeout": 60, "enabled": True,
        }},
        state_dir=tmp_path, run_workflow=slow, clock=clock,
    )
    clock.advance(seconds=45)
    await sched.tick()
    await asyncio.sleep(0.05)
    assert started == ["prompt"]
    assert cancelled == []

    clock.advance(minutes=1)
    await sched.tick()
    # Wait for cancellation to land + replacement to start.
    for _ in range(50):
        if cancelled and len(started) >= 2:
            break
        await asyncio.sleep(0.01)
    assert cancelled == ["prompt"]
    assert len(started) == 2
