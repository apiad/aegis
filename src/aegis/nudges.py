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
the person owns the next move, or a fact explains the silence. So does a turn
the person interrupted, one that failed, and one that ran the person's own
slash command. Queue workers get neither: one whose turn ended with nothing
pending is finished and its result delivered, and a nudge would reopen it.
Only a live idle session is nudged; a stopped one was stopped by someone.

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

from .monitors import iso_now
from .transcript.entries import NUDGE_HEADER

if TYPE_CHECKING:
    from .registry import Registry

log = logging.getLogger("aegis.nudges")
IDLE_S = 600.0
PLAN_STALE_S = 900.0
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


def due(
    standing: dict, *, attention: str, worker: bool, idle: bool, now: float
) -> str | None:
    """The nudge a session needs now: ``"plan"``, ``"idle"`` or None."""
    ended = standing.get("ended")
    if worker or not idle or not ended or attention not in ("done", "review"):
        return None
    if ended["how"] not in ("reported", "silent") or ended.get("at") is None:
        return None
    nudged = standing.get("nudged") or []
    if (
        "plan" not in nudged
        and any(i["state"] != "done" for i in standing.get("plan") or [])
        and stale_s(standing) >= PLAN_STALE_S
    ):
        return "plan"
    if (
        ended["how"] == "silent"
        and "idle" not in nudged
        and now - ended["at"] >= IDLE_S
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
    mins = round((now - standing["ended"]["at"]) / 60)
    return (
        f"You have been idle for {mins} minutes and your last turn did not say how "
        "it ended. What are you doing: done, needs you, or something else? Answer "
        "with turn_end. aegis asks this once."
    )


class Nudges:
    def __init__(self, registry: "Registry") -> None:
        self._registry = registry
        self._task: asyncio.Task | None = None

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

    async def tick(self) -> None:
        now = time.time()
        for s in list(self._registry.sessions.values()):
            kind = due(
                s.standing,
                attention=self._registry.card(s)["attention"],
                worker=bool(s.worker),
                idle=s.status == "idle" and s.running and not s.held,
                now=now,
            )
            if kind is None:
                continue
            log.info("aegis: nudging %s (%s)", s.handle, kind)
            try:
                await s.deliver(
                    f"{NUDGE_HEADER}{kind} · {iso_now()}", body(kind, s.standing, now)
                )
            except Exception:
                log.exception("aegis: could not nudge %s", s.handle)
