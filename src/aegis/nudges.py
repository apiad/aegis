"""Nudges: aegis asking an agent that has drifted to say where it stands.

Two, each delivered through the inbox as ``> from aegis:nudge · <kind> · <ts>``,
so it renders as an inbox row and never as the person's message:

- ``plan``: the last turn ended, the plan still has items not done, and the
  agent has worked ``PLAN_STALE_S`` since it last called ``plan_update``. Work
  is the plan clock's measure (transcript/plan_clock.py), so hours spent
  waiting on the person never make a plan stale.
- ``idle``: the last turn ended with no ``turn_end`` and the session has sat
  ``IDLE_S`` since with nothing to wait on. The card reads such a turn as done,
  whether it was or not; the agent is asked once which it was.

A session whose card says needs_you, waiting, working or error gets neither:
the person owns the next move, or a fact explains the silence. The main agent
of a running workflow is waiting on its run (registry._waits), so it is
never nudged while the engine is the one that will wake it. So does a turn
the person interrupted, one that failed, and one that ran the person's own
slash command. Queue workers get neither: one whose turn ended with nothing
pending is finished and its result delivered, and a nudge would reopen it.
Only an idle session is nudged, live or asleep after a park; one that slept was put
to sleep by someone.

Neither can loop. The fold keeps which nudges were sent (``nudged`` in the
standing): a send that carries no nudge re-arms ``idle``, and only a plan
record re-arms ``plan``. A nudge's own turn ending silently is therefore not a
new idle stretch, and between two messages that are not nudges a session gets
at most one of each. The facts come from the store, so a restart neither
repeats a nudge nor forgets one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from . import notice_sources
from .monitors import iso_now
from .notice_sources import Item
from .transcript.entries import NUDGE_HEADER

if TYPE_CHECKING:
    from .config import Config
    from .registry import Registry
    from .session import Session

log = logging.getLogger("aegis.nudges")
EVERY_S = 15.0


def stale_s(standing: dict) -> float:
    """Work since the last plan record, as of the end of the last turn. The
    clock accrues only when what it runs changes, and after a turn that did
    not hand back it keeps running work, so add the stretch up to the end."""
    clock, mark, ended = (
        standing.get("clock"),
        standing.get("plan_mark"),
        standing.get("ended"),
    )
    if clock is None or mark is None or not ended or ended.get("at") is None:
        return 0.0
    run = clock["running"] == "work" and ended["at"] > clock["at"]
    return clock["work_s"] + (ended["at"] - clock["at"] if run else 0.0) - mark


def stale_now(standing: dict, now: float) -> float:
    """Work since the last plan record, as of ``now``: the clock's total
    plus the stretch it is running, when it runs work."""
    clock, mark = standing.get("clock"), standing.get("plan_mark")
    if clock is None or mark is None:
        return 0.0
    run = clock["running"] == "work" and now > clock["at"]
    return clock["work_s"] + (now - clock["at"] if run else 0.0) - mark


# The plan's per-session notices (notices.py): where it stands as a status
# line, and a warning when it has gone ``stale_min`` minutes of work untouched.
SOURCE = "plan"
notice_sources.declare(SOURCE, {"stale_min": 10.0})


def plan_items(standing: dict, cfg: dict, now: float) -> list[Item]:
    plan = standing.get("plan") or []
    open_ = [i for i in plan if i["state"] != "done"]
    if not open_:
        return []
    done = len(plan) - len(open_)
    doing = next((i for i in open_ if i["state"] == "doing"), None)
    now_text = f"doing: {doing['text']}" if doing else f"next: {open_[0]['text']}"
    items = [Item("progress", "info", f"plan {done}/{len(plan)} · {now_text}")]
    stale = stale_now(standing, now)
    if stale >= cfg.get("stale_min", 10.0) * 60:
        items.append(
            Item(
                "stale",
                "warn",
                f"plan untouched for {round(stale / 60)} min of work · "
                "update it with plan_update",
            )
        )
    return items


def due(
    standing: dict,
    *,
    attention: str,
    worker: bool,
    idle: bool,
    now: float,
    silent_s: float | None,
    plan_s: float,
) -> str | None:
    """The nudge a session needs now: ``"plan"``, ``"idle"`` or None.
    ``silent_s`` None turns the idle nudge off."""
    ended = standing.get("ended")
    if worker or not idle or not ended or attention not in ("done", "review"):
        return None
    if ended["how"] not in ("reported", "silent") or ended.get("at") is None:
        return None
    nudged = standing.get("nudged") or []
    if (
        "plan" not in nudged
        and any(i["state"] != "done" for i in standing.get("plan") or [])
        and stale_s(standing) >= plan_s
    ):
        return "plan"
    if (
        silent_s is not None
        and ended["how"] == "silent"
        and "idle" not in nudged
        and now - ended["at"] >= silent_s
    ):
        return "idle"
    return None


def body(kind: str, standing: dict, now: float) -> str:
    if kind == "plan":
        mins = round(stale_s(standing) / 60)
        return (
            f"Your plan has not changed in {mins} minutes of work and still has "
            "items not done. Update it with plan_update, or carry on with the item "
            "in progress. This reminder comes once per plan."
        )
    return (
        "Your last turn ended without turn_end, so your card reads done. If it was "
        "done, say so with turn_end; if you are waiting or need the person, say "
        "that. aegis asks this once."
    )


class Nudges:
    def __init__(self, registry: "Registry", config: "Config") -> None:
        self._registry = registry
        self._config = config
        self._task: asyncio.Task | None = None

    def timings(self) -> tuple[float | None, float]:
        """``(silent_s, plan_s)`` from the live ``nudges:`` block; silent_s
        None when the idle nudge is off."""
        cfg = self._config.current().nudges
        silent = cfg["silent_s"]
        return (None if silent is False else float(silent)), cfg["plan_min"] * 60.0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a tick must never end the loop
                log.exception("aegis: nudge tick failed")
            await asyncio.sleep(EVERY_S)

    def refresh(self, s: "Session") -> None:
        """Set the session's plan notices from its standing now."""
        notices = self._registry.notices
        if notices is None:
            return
        cfg = notices.cfg(SOURCE)
        items = [] if cfg is None else plan_items(s.standing, cfg, time.time())
        notices.set(s.log_id, SOURCE, items)

    async def tick(self) -> None:
        now = time.time()
        for s in list(self._registry.sessions.values()):
            self.refresh(s)
        for s in list(self._registry.sessions.values()):
            await self.check(s, now)

    async def check(self, s: "Session", now: float | None = None) -> None:
        """Nudge ``s`` when it is due one now."""
        now = time.time() if now is None else now
        if s.archived:
            return
        silent_s, plan_s = self.timings()
        kind = due(
            s.standing,
            attention=self._registry.card(s)["attention"],
            worker=bool(s.worker),
            idle=(s.status == "idle" and s.running or s.asleep_why == "idle")
            and not s.held,
            now=now,
            silent_s=silent_s,
            plan_s=plan_s,
        )
        if kind is None or self._registry.workflow_lock(s) is not None:
            return  # a workflow's node is the engine's to nudge
        log.info("aegis: nudging %s (%s)", s.handle, kind)
        try:
            await s.deliver(
                f"{NUDGE_HEADER}{kind} · {iso_now()}", body(kind, s.standing, now)
            )
        except Exception:
            log.exception("aegis: could not nudge %s", s.handle)
