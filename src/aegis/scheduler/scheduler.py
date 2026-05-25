"""Scheduler — cron-style scheduled workflow execution.

Owns a tick loop (default 60s cadence). Each tick walks the loaded
schedule table, dispatches fire-eligible entries via the supplied
``run_workflow`` coroutine, and appends lifecycle events to
``<state_dir>/schedules/<name>.jsonl``.

A derived snapshot at ``<state_dir>/schedules.snapshot.json`` carries
the next-fire-time + in-flight flag per schedule; dashboards read
that without re-parsing JSONL.

VS3 covers: ``cron`` triggers, ``lifecycle: forever``,
``on_overlap: skip``. VS4 will add ``fire_at``, the other lifecycle
forms, ``on_overlap: queue|kill``, timeouts, notify, and boot
replay/backfill.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from aegis.scheduler.clock import Clock, SystemClock
from aegis.scheduler.cron import next_fire as compute_next_fire
from aegis.scheduler.lifecycle import is_exhausted
from aegis.scheduler.notify import Notifier, maybe_notify
from aegis.scheduler.replay import replay_state

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    tick_seconds: int = 60
    default_timezone: str = "UTC"


@dataclass
class _SchedState:
    """Per-schedule runtime state. Lives in memory; snapshot is its
    serialized form."""
    next_fire: datetime
    fire_count: int = 0
    in_flight: bool = False
    last_status: str | None = None
    last_completed_at: datetime | None = None


RunWorkflow = Callable[[str, dict[str, Any]], Awaitable[Any]]


class Scheduler:
    """Single-asyncio-task scheduler.

    ``schedules`` is the loaded entry table (``{name: entry_dict}``);
    ``run_workflow(name, args)`` is the dispatch hook the substrate
    calls when a schedule fires. ``state_dir`` is the root for
    JSONL + snapshot writes.
    """

    def __init__(
        self,
        *,
        schedules: dict[str, dict[str, Any]],
        state_dir: Path,
        run_workflow: RunWorkflow,
        clock: Clock | None = None,
        cfg: SchedulerConfig | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.schedules = dict(schedules)
        self.state_dir = Path(state_dir)
        self.run_workflow = run_workflow
        self.clock = clock or SystemClock()
        self.cfg = cfg or SchedulerConfig()
        self.notifier = notifier
        self._state: dict[str, _SchedState] = {}
        self._fire_tasks: dict[str, asyncio.Task] = {}
        self._deferred: dict[str, list[dict]] = {}
        self._task: asyncio.Task | None = None
        self._stopped: asyncio.Event | None = None
        self._init_state()

    # ── state ──────────────────────────────────────────────────────
    def _init_state(self) -> None:
        """Replay JSONL logs to rebuild fire_count + next_fire."""
        now = self.clock.now()
        replay = replay_state(self.state_dir, schedules=self.schedules,
                              now=now)
        for name, slot in replay.items():
            self._state[name] = _SchedState(
                next_fire=slot["next_fire"],
                fire_count=slot["fire_count"])

    def _compute_next(self, entry: dict, after: datetime) -> datetime:
        tz = entry.get("timezone", self.cfg.default_timezone)
        return compute_next_fire(entry["cron"], tz, after)

    # ── tick ───────────────────────────────────────────────────────
    async def tick(self) -> None:
        """Walk every schedule once. Dispatch eligible entries."""
        now = self.clock.now()
        for name, entry in self.schedules.items():
            st = self._state.get(name)
            if st is None:
                continue
            if not entry.get("enabled", True):
                continue
            if is_exhausted(entry.get("lifecycle", "forever"),
                            fire_count=st.fire_count, now=now):
                continue
            if st.next_fire > now:
                continue
            if st.in_flight:
                policy = entry.get("on_overlap", "skip")
                if policy == "skip":
                    st.next_fire = self._advance_next(entry, now)
                    continue
                if policy == "queue":
                    self._deferred.setdefault(name, []).append(entry)
                    st.next_fire = self._advance_next(entry, now)
                    continue
                if policy == "kill":
                    prior = self._fire_tasks.get(name)
                    if prior is not None and not prior.done():
                        prior.cancel()
                # fall through to fire a new task
            self._dispatch(name, entry)
            st.next_fire = self._advance_next(entry, now)
        self._write_snapshot()

    def _dispatch(self, name: str, entry: dict) -> None:
        """Spawn a _fire task and track it for on_overlap=kill."""
        task = asyncio.create_task(self._fire(name, entry))
        self._fire_tasks[name] = task

    def _advance_next(self, entry: dict, now: datetime) -> datetime:
        """Next fire after dispatch. For cron, compute strictly after
        ``now``. For ``fire_at`` (no cron), park at datetime.max so the
        entry won't refire — lifecycle handles exhaustion separately."""
        if "cron" in entry:
            return self._compute_next(entry, now)
        return datetime.max.replace(tzinfo=now.tzinfo)

    def replace_schedules(
        self, new_schedules: dict[str, dict[str, Any]],
    ) -> None:
        """Atomic-swap the loaded schedule table.

        New entries get fresh ``next_fire`` computed from the current
        clock. Existing entries keep their fire_count + next_fire (so
        a reload that only touches an unrelated entry doesn't reset
        the world). Removed entries are dropped from state.

        Concurrency: callers serialize via the reload watcher's
        debounce — no internal lock.
        """
        new_state: dict[str, _SchedState] = {}
        now = self.clock.now()
        for name, entry in new_schedules.items():
            prior = self._state.get(name)
            if prior is not None and self.schedules.get(name) == entry:
                new_state[name] = prior
                continue
            # New or changed: recompute next_fire from scratch.
            if "fire_at" in entry:
                dt = datetime.fromisoformat(entry["fire_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=now.tzinfo)
                nxt = dt
            elif "cron" in entry:
                nxt = self._compute_next(entry, now)
            else:
                nxt = datetime.max.replace(tzinfo=now.tzinfo)
            fire_count = prior.fire_count if prior is not None else 0
            new_state[name] = _SchedState(
                next_fire=nxt, fire_count=fire_count)
        self.schedules = dict(new_schedules)
        self._state = new_state
        # Drop deferred queues for removed entries.
        for k in list(self._deferred):
            if k not in self._state:
                del self._deferred[k]

    def fire_now(self, name: str) -> None:
        """Manually dispatch a schedule outside the tick cadence.

        Used by the TUI `F` action and the `aegis schedule run` CLI to
        force a fire. Records ``manual=True`` in the JSONL so audit
        trails distinguish operator-triggered fires from cron-triggered
        ones.
        """
        entry = self.schedules.get(name)
        if entry is None:
            raise KeyError(f"unknown schedule: {name!r}")
        asyncio.create_task(self._fire(name, entry, manual=True))

    async def _fire(self, name: str, entry: dict, *,
                    manual: bool = False) -> None:
        st = self._state[name]
        st.in_flight = True
        task_id = uuid.uuid4().hex
        self._append_jsonl(name, {
            "ts": self.clock.now().isoformat(),
            "schedule": name,
            "event": "fire_requested",
            "task_id": task_id,
            "manual": manual,
            "backfilled": False,
        })
        try:
            result = await self.run_workflow(
                entry["workflow"], dict(entry.get("args") or {}))
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name,
                "event": "fire_completed",
                "task_id": task_id,
                "status": "ok",
                "result_excerpt": str(result)[:500] if result else "",
            })
            st.last_status = "ok"
        except asyncio.CancelledError:
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name,
                "event": "fire_failed",
                "task_id": task_id,
                "status": "failed:killed",
            })
            st.last_status = "failed:killed"
            raise
        except Exception as e:  # noqa: BLE001 — any error becomes failed:crash
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name,
                "event": "fire_failed",
                "task_id": task_id,
                "status": "failed:crash",
                "error": repr(e),
            })
            st.last_status = "failed:crash"
            logger.exception("scheduler fire failed: %s", name)
        finally:
            st.fire_count += 1
            st.last_completed_at = self.clock.now()
            st.in_flight = False
            maybe_notify(self.notifier, entry, schedule=name,
                         status=st.last_status or "unknown")
            # Drain one deferred (queued) fire if any.
            deferred = self._deferred.get(name)
            if deferred:
                next_entry = deferred.pop(0)
                self._dispatch(name, next_entry)

    # ── persistence ────────────────────────────────────────────────
    def _append_jsonl(self, name: str, record: dict) -> None:
        path = self.state_dir / "schedules" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def _write_snapshot(self) -> None:
        snap = {
            name: {
                "next_fire": st.next_fire.isoformat(),
                "fire_count": st.fire_count,
                "in_flight": st.in_flight,
                "last_status": st.last_status,
                "last_completed_at": (
                    st.last_completed_at.isoformat()
                    if st.last_completed_at else None),
            }
            for name, st in self._state.items()
        }
        path = self.state_dir / "schedules.snapshot.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap, indent=2))
        tmp.replace(path)

    # ── lifecycle ──────────────────────────────────────────────────
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._stopped is not None
        while not self._stopped.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — never crash the loop
                logger.exception("scheduler tick crashed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self.cfg.tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        if self._stopped is None:
            return
        self._stopped.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._task = None
