"""The schedule operations (schedules.py), for people and, as MCP tools, for
agents.

Reading is open to anyone, as with sessions. Changing follows the rule the
queues apply to tasks (agent_ops.mine): an agent changes only the schedules it
created, a member only the ones it owns, an admin any. A schedule an agent
creates records the agent's permission as its ceiling, and a new fire never
starts an agent above it. A run with ``spawn: false`` may not call
schedule_create (ops.NO_SPAWN), or it could start agents through a schedule.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .clock import ClockError, clock_from
from .ops import Caller, NoParams, OpError
from .quota_gate import pools_wire
from .schedule_log import DEFAULT_QUOTA, Check, Definition

if TYPE_CHECKING:
    from .app import App

NAME = "Two or three lowercase words joined by hyphens, unique on this server."


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ScheduleName(_Strict):
    name: str


class ScheduleResume(ScheduleName):
    workflow: str | None = Field(
        None,
        description="The version of the workflow this schedule fires, as its card shows it.",
    )


class ScheduleDef(Definition):
    name: str = Field(description=NAME)


class ScheduleRuns(_Strict):
    name: str
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)
    quiet: bool = Field(False, description="Include the ticks that changed nothing.")


class ScheduleTest(_Strict):
    clock: dict[str, str]
    check: Check | None = None
    session: str | None = Field(
        None, description="An existing target, whose directory the check runs in."
    )
    cwd: str | None = None
    name: str | None = Field(
        None,
        description="The schedule being edited, whose last fire the check compares with.",
    )


def register_schedule_ops(app: App) -> None:
    r, sch, reg = app.registry, app.schedules, app.sessions

    def me(caller: Caller):
        return reg.sessions.get(caller.log_id or "") if caller.is_agent else None

    def owner(caller: Caller) -> dict:
        who, s = app.who(caller), me(caller)
        if s is None:
            return {"kind": "user", "principal": who.name}
        return {
            "kind": "agent",
            "log_id": s.log_id,
            "principal": who.name,
            "ceiling": s.spec.permission,
        }

    def cwd(raw: str | None, caller: Caller) -> Path:
        s = me(caller)
        return app._resolve_cwd(raw, s.spec.cwd if s else None)

    def mine(name: str, caller: Caller):
        s = sch.items.get(name)
        if s is None:
            raise OpError("no_schedule", f"no schedule {name!r}")
        o = s.owner
        if caller.is_agent and (
            o.get("kind") != "agent" or o.get("log_id") != caller.log_id
        ):
            raise OpError(
                "not_yours", "agents can change only the schedules they created"
            )
        who = app.who(caller)
        if not who.is_admin and o.get("principal") != who.name:
            raise OpError(
                "not_owner", f"{name} belongs to {o.get('principal')}; you can read it"
            )
        return s

    def person_only(name: str, caller: Caller, what: str) -> None:
        if caller.is_agent and sch.awaiting(name):
            raise OpError(
                "needs_person",
                f"{name} waits for a person's approval; only a person can {what} "
                "it, from the card under the call that made it",
            )

    def card(caller: Caller, name: str, action: str) -> None:
        # The approval card under the agent's call (entries.py, entries.js).
        if (s := me(caller)) is not None:
            s.record_card(
                {
                    "kind": "approval",
                    "object": "schedule",
                    "name": name,
                    "action": action,
                }
            )

    def definition(p: ScheduleDef) -> Definition:
        return Definition.model_validate(p.model_dump(exclude={"name"}))

    @r.op("schedule.list", NoParams, agent=True, read=True)
    async def schedule_list(_, caller):
        """Every schedule on this server: its clock, layers and owner, its next
        tick, its live runs, and whether it is paused."""
        return sch.wire_all()

    @r.op("schedule.runs", ScheduleRuns, agent=True, read=True)
    async def schedule_runs(p: ScheduleRuns, caller):
        """One schedule's fires and ticks, newest first, a page at a time. The
        ticks that changed nothing are left out unless you pass quiet."""
        return sch.runs(p.name, p.offset, p.limit, p.quiet)

    @r.op("schedule.pools", NoParams, read=True)
    async def schedule_pools(_, caller):
        """The pools a schedule's quota lines may name, with their windows, and
        the lines a schedule gets when it names none: for the view's editor."""
        return {
            "pools": [p for p in pools_wire() if p["windows"]],
            "default": DEFAULT_QUOTA,
        }

    @r.op("schedule.create", ScheduleDef, agent=True)
    async def schedule_create(p: ScheduleDef, caller):
        """Fire a prompt on a clock: every 5m, a cron line, or once at a time,
        into a new session from an agent or into an existing session's inbox.
        With a bash check, a tick fires only when the check exits 0 with a
        stdout different from the last fire's, and that stdout is appended to
        the prompt. You own what you create; schedule_delete removes it. A
        schedule you create or change waits, paused, until a person approves it
        from the card under your call."""
        d = definition(p)
        s = sch.create(p.name, d, owner(caller), cwd(d.fire.cwd, caller))
        card(caller, p.name, "created")
        return sch.wire(s.name)

    @r.op("schedule.update", ScheduleDef, agent=True)
    async def schedule_update(p: ScheduleDef, caller):
        """Replace a schedule you own with a new definition. It applies from the
        next tick; a live run is not touched."""
        mine(p.name, caller)
        d = definition(p)
        sch.update(p.name, d, cwd(d.fire.cwd, caller), by_agent=caller.is_agent)
        card(caller, p.name, "updated")
        return sch.wire(p.name)

    @r.op("schedule.delete", ScheduleName, agent=True)
    async def schedule_delete(p: ScheduleName, caller):
        """Delete a schedule you own. A live run keeps going."""
        mine(p.name, caller)
        sch.delete(p.name)
        return {"deleted": p.name}

    @r.op("schedule.pause", ScheduleName, agent=True)
    async def schedule_pause(p: ScheduleName, caller):
        """Stop a schedule you own from ticking until it is resumed."""
        mine(p.name, caller)
        sch.pause(p.name)
        return sch.wire(p.name)

    @r.op("schedule.resume", ScheduleResume, agent=True)
    async def schedule_resume(p: ScheduleResume, caller):
        """Start a paused schedule again from now, its error count cleared. The
        ticks that fell during the pause do not fire."""
        s = mine(p.name, caller)
        person_only(p.name, caller, "unpause")
        if caller.is_agent and sch.workflow_of(s.d.prompt) and not s.pin:
            raise OpError(
                "needs_person",
                f"{p.name} fires a workflow no person has approved a version of; "
                "only a person can unpause it",
            )
        sch.resume(p.name, p.workflow)
        return sch.wire(p.name)

    @r.op("schedule.fire", ScheduleName, agent=True)
    async def schedule_fire(p: ScheduleName, caller):
        """Fire a schedule you own now, skipping its clock and its check. The
        overlap rule still holds: with a live run, wait holds it and skip drops it.
        A schedule that fires a workflow fires only on its clock for an agent:
        the person approved the clock, not runs on demand."""
        s = mine(p.name, caller)
        person_only(p.name, caller, "fire")
        if caller.is_agent and sch.workflow_of(s.d.prompt) is not None:
            raise OpError(
                "needs_person",
                f"{p.name} fires a workflow, so only its clock or a person fires it",
            )
        return {"outcome": await sch.fire_now(p.name), **sch.wire(p.name)}

    @r.op("schedule.test", ScheduleTest, agent=True)
    async def schedule_test(p: ScheduleTest, caller):
        """Try a clock and a check without saving anything: the next three
        ticks, and whether the check would fire now and why."""
        try:
            clock = clock_from(p.clock)
        except ClockError as e:
            return {"error": str(e)}
        if p.session:
            s = reg.sessions.get(p.session) or reg.by_handle(p.session)
            if s is None:
                raise OpError("no_session", f"no open session {p.session!r}")
            where = s.spec.cwd
        else:
            where = cwd(p.cwd, caller)
        user = app.principals.linux_user_of(app.who(caller).name)
        return await sch.test(clock, p.check, where, p.name, user)
