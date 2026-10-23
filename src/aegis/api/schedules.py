"""Schedules as objects: ``srv.schedules`` and the ``Schedule`` handle.

``create`` and ``update`` take the definition schedule_create takes, flattened
into keyword arguments: one of ``every``, ``cron`` or ``at`` for the clock,
``session`` for a fire into an existing session's inbox (else a new session
from ``agent``), and ``check`` for the bash a tick runs first. A schedule a
program creates is the admin's, so it starts unpaused, as a person's does.
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any, Literal

from .models import ScheduleInfo
from .sessions import Session

if TYPE_CHECKING:
    from ..server import Server


class ScheduleNotFound(LookupError):
    """No schedule has that name."""


def definition(
    name: str,
    *,
    prompt: str,
    every: str | None,
    cron: str | None,
    at: str | None,
    agent: str | None,
    session: Session | None,
    cwd: str | None,
    close: str,
    spawn: bool,
    overlap: str,
    check: str | None,
    check_timeout_s: int,
    quota: dict | None,
) -> dict[str, Any]:
    clock = {k: v for k, v in (("every", every), ("cron", cron), ("at", at)) if v}
    if len(clock) != 1:
        raise ValueError("give exactly one of every, cron or at")
    fire: dict[str, Any] = {"close": close, "spawn": spawn}
    if session is not None:
        fire |= {"target": "existing", "session": session.log_id}
    else:
        fire |= {"target": "new", "agent": agent, "cwd": cwd}
    d: dict[str, Any] = {
        "name": name,
        "clock": clock,
        "prompt": prompt,
        "overlap": overlap,
        "fire": {k: v for k, v in fire.items() if v is not None},
    }
    if check is not None:
        d["check"] = {"bash": check, "timeout_s": check_timeout_s}
    if quota is not None:
        d["quota"] = quota
    return d


class Schedules:
    """The schedules on one server."""

    def __init__(self, server: Server) -> None:
        self._srv = server

    async def create(
        self,
        name: str,
        *,
        prompt: str,
        every: str | None = None,
        cron: str | None = None,
        at: str | None = None,
        agent: str | None = None,
        session: Session | None = None,
        cwd: str | None = None,
        close: Literal["done", "always", "never"] = "done",
        spawn: bool = False,
        overlap: Literal["wait", "skip", "parallel"] = "wait",
        check: str | None = None,
        check_timeout_s: int = 30,
        quota: dict | None = None,
    ) -> Schedule:
        """Fire ``prompt`` on a clock: ``every="5m"``, ``cron="0 6 * * 1-5"`` or
        ``at="2026-10-15 09:00"``, in the server's local time."""
        d = definition(
            name,
            prompt=prompt,
            every=every,
            cron=cron,
            at=at,
            agent=agent,
            session=session,
            cwd=cwd,
            close=close,
            spawn=spawn,
            overlap=overlap,
            check=check,
            check_timeout_s=check_timeout_s,
            quota=quota,
        )
        await self._srv._call("schedule.create", d)
        return Schedule(self._srv, name)

    async def get(self, name: str) -> Schedule:
        if name not in self._srv.app.schedules.items:
            raise ScheduleNotFound(f"no schedule {name!r}")
        return Schedule(self._srv, name)

    async def list(self) -> builtins.list[Schedule]:
        return [Schedule(self._srv, n) for n in sorted(self._srv.app.schedules.items)]


class Schedule:
    """A live handle on one schedule, by name."""

    def __init__(self, server: Server, name: str) -> None:
        self._srv = server
        self.name = name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Schedule) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"Schedule({self.name!r})"

    async def info(self) -> ScheduleInfo:
        if self.name not in self._srv.app.schedules.items:
            raise ScheduleNotFound(f"no schedule {self.name!r}")
        return ScheduleInfo.model_validate(self._srv.app.schedules.wire(self.name))

    async def update(
        self,
        *,
        prompt: str,
        every: str | None = None,
        cron: str | None = None,
        at: str | None = None,
        agent: str | None = None,
        session: Session | None = None,
        cwd: str | None = None,
        close: Literal["done", "always", "never"] = "done",
        spawn: bool = False,
        overlap: Literal["wait", "skip", "parallel"] = "wait",
        check: str | None = None,
        check_timeout_s: int = 30,
        quota: dict | None = None,
    ) -> ScheduleInfo:
        """Replace the definition; it applies from the next tick."""
        d = definition(
            self.name,
            prompt=prompt,
            every=every,
            cron=cron,
            at=at,
            agent=agent,
            session=session,
            cwd=cwd,
            close=close,
            spawn=spawn,
            overlap=overlap,
            check=check,
            check_timeout_s=check_timeout_s,
            quota=quota,
        )
        return ScheduleInfo.model_validate(await self._srv._call("schedule.update", d))

    async def pause(self) -> ScheduleInfo:
        return ScheduleInfo.model_validate(
            await self._srv._call("schedule.pause", {"name": self.name})
        )

    async def resume(self) -> ScheduleInfo:
        """Start ticking again from now; the ticks missed while paused do not fire."""
        return ScheduleInfo.model_validate(
            await self._srv._call("schedule.resume", {"name": self.name})
        )

    async def fire(self) -> str:
        """Fire once now, skipping the clock and the check; the overlap rule
        still holds. Returns what happened (sch.fire_now's outcome)."""
        r = await self._srv._call("schedule.fire", {"name": self.name})
        return str(r["outcome"])

    async def delete(self) -> None:
        """Delete the schedule; a live run keeps going."""
        await self._srv._call("schedule.delete", {"name": self.name})
