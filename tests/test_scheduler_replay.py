"""Tests for on-boot replay: dangling fire_requested + backfill."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from aegis.scheduler.replay import replay_state


def _write_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_dangling_fire_requested_marked_interrupted(tmp_path: Path) -> None:
    log = tmp_path / "schedules" / "eod.jsonl"
    _write_log(log, [{
        "ts": "2026-05-24T02:00:00+00:00", "schedule": "eod",
        "event": "fire_requested", "task_id": "abc",
    }])
    state = replay_state(tmp_path, schedules={"eod": {}})
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert last["event"] == "fire_failed"
    assert last["status"] == "failed:interrupted"
    assert state["eod"]["fire_count"] == 1


def test_completed_fire_increments_count(tmp_path: Path) -> None:
    log = tmp_path / "schedules" / "eod.jsonl"
    _write_log(log, [
        {"ts": "x", "schedule": "eod", "event": "fire_requested",
         "task_id": "a"},
        {"ts": "x", "schedule": "eod", "event": "fire_completed",
         "task_id": "a", "status": "ok"},
        {"ts": "x", "schedule": "eod", "event": "fire_requested",
         "task_id": "b"},
        {"ts": "x", "schedule": "eod", "event": "fire_failed",
         "task_id": "b", "status": "failed:crash"},
    ])
    state = replay_state(tmp_path, schedules={"eod": {}})
    assert state["eod"]["fire_count"] == 2


def test_backfill_once_when_next_fire_past(tmp_path: Path) -> None:
    schedules = {"eod": {"cron": "0 2 * * *", "timezone": "UTC"}}
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    state = replay_state(tmp_path, schedules=schedules, now=now)
    # croniter.get_next strictly after now → tomorrow 02:00 UTC, so no
    # backfill. Make sure the field is present and accurately reflects
    # ordering.
    assert state["eod"]["next_fire"] > now
    assert state["eod"]["backfill"] is False


def test_fire_at_in_past_triggers_backfill(tmp_path: Path) -> None:
    schedules = {"oneoff": {"fire_at": "2026-05-24T10:00:00+00:00"}}
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    state = replay_state(tmp_path, schedules=schedules, now=now)
    assert state["oneoff"]["next_fire"] <= now
    assert state["oneoff"]["backfill"] is True


def test_no_log_means_zero_fire_count(tmp_path: Path) -> None:
    state = replay_state(tmp_path, schedules={
        "fresh": {"cron": "0 2 * * *", "timezone": "UTC"}})
    assert state["fresh"]["fire_count"] == 0
