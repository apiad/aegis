"""Tests for ReloadWatcher + Scheduler.replace_schedules."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aegis.scheduler.clock import FakeClock
from aegis.scheduler.reload import ReloadWatcher
from aegis.scheduler.scheduler import Scheduler


def _entry(**kw) -> dict:
    base = {
        "workflow": "prompt", "args": {},
        "cron": "0 2 * * *", "timezone": "UTC",
        "lifecycle": "forever", "on_overlap": "skip",
        "enabled": True,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_watcher_fires_on_yaml_write(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    triggered = asyncio.Event()

    async def on_reload():
        triggered.set()

    watcher = ReloadWatcher(tmp_path, on_reload=on_reload,
                            debounce_seconds=0.1)
    await watcher.start()
    try:
        overlay = tmp_path / ".aegis" / "schedules"
        (overlay / "new.yaml").write_text(
            "workflow: prompt\ncron: '* * * * *'\n")
        await asyncio.wait_for(triggered.wait(), timeout=3.0)
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_swallows_reload_exception(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    seen = asyncio.Event()
    calls = 0

    def on_reload():
        nonlocal calls
        calls += 1
        seen.set()
        raise RuntimeError("synthetic")

    events_log = tmp_path / "events.jsonl"
    watcher = ReloadWatcher(tmp_path, on_reload=on_reload,
                            debounce_seconds=0.1,
                            events_log=events_log)
    await watcher.start()
    try:
        (tmp_path / ".aegis" / "schedules" / "bad.yaml").write_text(
            "workflow: x\n")
        await asyncio.wait_for(seen.wait(), timeout=3.0)
        # Watcher loop must still be alive: trigger again.
        seen.clear()
        (tmp_path / ".aegis" / "schedules" / "bad.yaml").write_text(
            "workflow: y\n")
        await asyncio.wait_for(seen.wait(), timeout=3.0)
        assert calls >= 2
    finally:
        await watcher.stop()
    assert events_log.exists()
    text = events_log.read_text()
    assert "reload_failed" in text


def test_replace_schedules_preserves_unchanged_state(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()}, state_dir=tmp_path,
        run_workflow=fake_run, clock=clock)
    sched._state["eod"].fire_count = 7

    # Identical replacement should preserve the count + next_fire.
    sched.replace_schedules({"eod": _entry()})
    assert sched._state["eod"].fire_count == 7


def test_replace_schedules_resets_changed_entry(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()}, state_dir=tmp_path,
        run_workflow=fake_run, clock=clock)
    sched._state["eod"].fire_count = 3

    # Change the cron → fire_count preserved, next_fire recomputed.
    new_entry = _entry(cron="0 3 * * *")
    sched.replace_schedules({"eod": new_entry})
    assert sched._state["eod"].fire_count == 3
    assert sched.schedules["eod"]["cron"] == "0 3 * * *"


def test_replace_schedules_drops_removed_entry(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry(), "morn": _entry(cron="0 6 * * *")},
        state_dir=tmp_path, run_workflow=fake_run, clock=clock)
    sched.replace_schedules({"eod": _entry()})
    assert "morn" not in sched._state
    assert "eod" in sched._state


def test_replace_schedules_adds_new_entry(tmp_path: Path) -> None:
    async def fake_run(name, args):
        return "ok"

    clock = FakeClock(datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"eod": _entry()}, state_dir=tmp_path,
        run_workflow=fake_run, clock=clock)
    sched.replace_schedules({
        "eod": _entry(),
        "fresh": _entry(cron="*/5 * * * *"),
    })
    assert "fresh" in sched._state
    assert sched._state["fresh"].fire_count == 0
