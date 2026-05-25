"""On-boot replay of per-schedule JSONL logs.

When ``aegis serve`` restarts mid-flight, every schedule's JSONL is
walked once: a dangling ``fire_requested`` (no matching terminal
record) is closed out as ``fire_failed`` with status
``failed:interrupted`` so the count is honest and dashboards don't
show a forever-in-flight schedule. The fire_count is rebuilt; the
next_fire is recomputed from ``fire_at`` / ``cron``; and a
``backfill`` flag is raised when the next_fire is already in the past
(so the first tick triggers a single catch-up fire).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from aegis.scheduler.cron import next_fire as compute_next_fire


def replay_state(state_dir: Path, *, schedules: dict,
                 now: datetime | None = None) -> dict:
    """Returns ``{name: {fire_count, next_fire, backfill}}``."""
    now = now or datetime.now(timezone.utc)
    state: dict = {}
    sched_dir = state_dir / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)
    for name, entry in schedules.items():
        log = sched_dir / f"{name}.jsonl"
        fire_count = 0
        dangling: str | None = None
        if log.exists():
            for line in log.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                ev = rec.get("event")
                if ev == "fire_requested":
                    dangling = rec.get("task_id")
                elif ev in ("fire_completed", "fire_failed"):
                    fire_count += 1
                    dangling = None
        if dangling:
            interrupted = {
                "ts": now.isoformat(), "schedule": name,
                "event": "fire_failed", "task_id": dangling,
                "status": "failed:interrupted",
            }
            with log.open("a") as f:
                f.write(json.dumps(interrupted) + "\n")
            fire_count += 1

        if "fire_at" in entry:
            nf = datetime.fromisoformat(entry["fire_at"])
            if nf.tzinfo is None:
                nf = nf.replace(tzinfo=timezone.utc)
        elif "cron" in entry:
            tz = entry.get("timezone", "UTC")
            nf = compute_next_fire(entry["cron"], tz, now)
        else:
            nf = now

        state[name] = {
            "fire_count": fire_count,
            "next_fire": nf,
            "backfill": nf <= now,
        }
    return state
