"""The schedule operations (schedules.py), for people and, as MCP tools, for
agents.

Reading is open to anyone, as with sessions. Changing follows the rule the
queues apply to tasks (agent_ops.mine): an agent changes only the schedules it
created, a member only the ones it owns, an admin any. A schedule an agent
creates records the agent's permission as its ceiling, and a new fire never
starts an agent above it. A run with ``spawn: false`` may not call
schedule_create (ops.NO_SPAWN), or it could start agents through a schedule.

Across a trusted link an agent names a schedule ``name@server`` (agent_ops.across,
as ``queue@server``), and the operation runs there. The far server keeps every
rule, because the near server sends the agent as ``from_agent``: an agent there
makes a schedule that waits for a person, and changes only what it made. A call
through the link that names no agent is a person's, which is what a card's
Unpause in a browser on the near server is. The field is one a server older than
this one refuses, so such a call fails there rather than passing as a person's.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from .agent_ops import FarAgent, across, far_error, from_trusted, split_address
from .agents import PERMISSION_ORDER
from .clock import ClockError, clock_from
from .ops import Caller, NoParams, OpError
from .quota_gate import pools_wire
from .schedule_log import DEFAULT_QUOTA, Check, Definition

if TYPE_CHECKING:
    from .app import App

AT_SERVER = "`name@server` is one on a server linked with trust."
NAME = (
    "Two or three lowercase words joined by hyphens, unique on its server. " + AT_SERVER
)


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class FarOwner(FarAgent):
    """An agent on a server linked with trust, as that server's link names it.
    Its log id is what stays the same through a rename."""

    log_id: str
    permission: str


class ScheduleName(_Strict):
    name: str = Field(description="The schedule's name. " + AT_SERVER)
    # Set only by a trusted link, for the agent there that called.
    from_agent: SkipJsonSchema[FarOwner | None] = Field(None, exclude=True)


class ScheduleResume(ScheduleName):
    workflow: str | None = Field(
        None,
        description="The version of the workflow this schedule fires, as its card shows it.",
    )


class ScheduleDef(Definition):
    name: str = Field(description=NAME)
    # Set only by a trusted link, for the agent there that called.
    from_agent: SkipJsonSchema[FarOwner | None] = Field(None, exclude=True)


class ScheduleRuns(_Strict):
    name: str = Field(description="The schedule's name. " + AT_SERVER)
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
        description="The schedule being edited, whose last fire the check compares "
        "with. `name@server` tries it on a server linked with trust, and that "
        "schedule need not exist yet.",
    )


def register_schedule_ops(app: App) -> None:
    r, sch, reg = app.registry, app.schedules, app.sessions

    def me(caller: Caller):
        return reg.sessions.get(caller.log_id or "") if caller.is_agent else None

    def far_agent(p, caller: Caller) -> FarOwner | None:
        """The far agent a call speaks for, checked, or None."""
        if p.from_agent is None:
            return None
        from_trusted(app, caller, p.from_agent)
        if p.from_agent.permission not in PERMISSION_ORDER:
            raise OpError("bad_sender", "a sender's permission is one of ours")
        return p.from_agent

    async def far_call(op: str, p, caller: Caller, *, speaks: bool = True):
        """A schedule on a server linked with trust, ``name@server``: the call
        goes there, and what comes back carries the address. None when the
        schedule is here, with ``p.name`` left as its name on this server."""
        name, server = split_address(p.name or "", app.server_name)
        if server is None:
            if p.name:
                p.name = name
            return None
        link = across(app, server, caller)
        s = me(caller)
        if s is None:
            raise OpError("agents_only", "only an agent's session can do this")
        params = p.model_dump(exclude_unset=True) | {"name": name}
        if speaks:
            params["from_agent"] = {
                "handle": s.handle,
                "server": app.server_name,
                "log_id": s.log_id,
                "permission": s.spec.permission,
            }
        try:
            r = await link.call(op, params)
        except OpError as e:
            raise far_error(e, server, trusted=True) from None
        return {"result": addressed(r, server)}

    def addressed(r, server: str):
        """A far server's answer, with the name the agent can call it by."""
        if isinstance(r, dict) and "name" in r:
            return {**r, "name": f"{r['name']}@{server}", "server": server}
        if isinstance(r, dict) and "deleted" in r:
            return {"deleted": f"{r['deleted']}@{server}"}
        return r

    async def far_edit(op: str, action: str, p: ScheduleDef, caller: Caller):
        """A create or an update across: the card goes under the call here, and
        acts on the far server. A way back has no live view of that server in
        this one's browser, so the agent is told where the person approves."""
        name, server = split_address(p.name, app.server_name)
        far = await far_call(op, p, caller)
        if far is None or server is None:
            return far
        card(caller, name, action, server)
        if app.links.outbound(server) is None and isinstance(far["result"], dict):
            far["result"]["note"] = (
                f"a person approves this in the Schedules view of {server}: "
                "browsers on this server do not follow that server's schedules"
            )
        return far

    def owner(caller: Caller, far: FarOwner | None = None) -> dict:
        who, s = app.who(caller), me(caller)
        if far is not None:
            return {
                "kind": "agent",
                "far": {
                    "handle": far.handle,
                    "server": far.server,
                    "log_id": far.log_id,
                },
                "principal": who.name,
                "ceiling": far.permission,
            }
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

    def mine(name: str, caller: Caller, far: FarOwner | None = None):
        s = sch.items.get(name)
        if s is None:
            raise OpError("no_schedule", f"no schedule {name!r}")
        o = s.owner
        made = o.get("far") or {}
        if far is not None:
            refused = made.get("server") != far.server or (
                made.get("log_id") != far.log_id
            )
        else:
            refused = caller.is_agent and (
                o.get("kind") != "agent" or o.get("log_id") != caller.log_id
            )
        if refused:
            raise OpError(
                "not_yours", "agents can change only the schedules they created"
            )
        who = app.who(caller)
        if not who.is_admin and o.get("principal") != who.name:
            raise OpError(
                "not_owner", f"{name} belongs to {o.get('principal')}; you can read it"
            )
        return s

    def person_only(name: str, agent: bool, what: str) -> None:
        if agent and sch.awaiting(name):
            raise OpError(
                "needs_person",
                f"{name} waits for a person's approval; only a person can {what} "
                "it, from the card under the call that made it",
            )

    def card(caller: Caller, name: str, action: str, server: str | None = None) -> None:
        # The approval card under the agent's call (entries.py, entries.js).
        if (s := me(caller)) is not None:
            s.record_card(
                {
                    "kind": "approval",
                    "object": "schedule",
                    "name": name,
                    "action": action,
                    **({"server": server} if server else {}),
                }
            )

    def definition(p: ScheduleDef) -> Definition:
        return Definition.model_validate(p.model_dump(exclude={"name"}))

    @r.op("schedule.list", NoParams, agent=True, read=True)
    async def schedule_list(_, caller):
        """Every schedule on this server: its clock, layers and owner, its next
        tick, its live runs, and whether it is paused. An agent also gets each
        server's linked with trust, named `name@server` and with `server`."""
        who = app.who(caller)
        out = [
            w
            for w in sch.wire_all()
            if app.sees(who, sch.items[w["name"]].owner.get("principal"))
        ]
        if caller.is_agent and who.is_admin:
            out += await far_schedules()
        return out

    async def far_schedules() -> list[dict]:
        """Every trusted server's schedules; one that cannot answer now has
        none, as in session_list."""
        out = []
        for link in app.links.up():
            if not app.links.trusted(link.name):
                continue
            try:
                listed = await link.call("schedule.list")
            except OpError:
                continue
            for w in listed if isinstance(listed, list) else []:
                if isinstance(w, dict) and "name" in w:
                    out.append(addressed(w, link.name))
        return out

    @r.op("schedule.runs", ScheduleRuns, agent=True, read=True)
    async def schedule_runs(p: ScheduleRuns, caller):
        """One schedule's fires and ticks, newest first, a page at a time. The
        ticks that changed nothing are left out unless you pass quiet.
        `name@server` reads one on a server linked with trust."""
        if (
            far := await far_call("schedule.runs", p, caller, speaks=False)
        ) is not None:
            return far["result"]
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
        from the card under your call. `name@server` makes it on a server linked
        with trust: it lives and ticks there, its runs start there, and a
        directory or a session in it is that server's."""
        if (far := await far_edit("schedule.create", "created", p, caller)) is not None:
            return far["result"]
        d, by = definition(p), far_agent(p, caller)
        s = sch.create(p.name, d, owner(caller, by), cwd(d.fire.cwd, caller))
        card(caller, p.name, "created")
        return sch.wire(s.name)

    @r.op("schedule.update", ScheduleDef, agent=True)
    async def schedule_update(p: ScheduleDef, caller):
        """Replace a schedule you own with a new definition. It applies from the
        next tick; a live run is not touched. `name@server` is one on a server
        linked with trust."""
        if (far := await far_edit("schedule.update", "updated", p, caller)) is not None:
            return far["result"]
        by = far_agent(p, caller)
        mine(p.name, caller, by)
        d = definition(p)
        agent = caller.is_agent or by is not None
        sch.update(p.name, d, cwd(d.fire.cwd, caller), by_agent=agent)
        card(caller, p.name, "updated")
        return sch.wire(p.name)

    @r.op("schedule.delete", ScheduleName, agent=True)
    async def schedule_delete(p: ScheduleName, caller):
        """Delete a schedule you own. A live run keeps going. `name@server` is
        one on a server linked with trust."""
        if (far := await far_call("schedule.delete", p, caller)) is not None:
            return far["result"]
        mine(p.name, caller, far_agent(p, caller))
        sch.delete(p.name)
        return {"deleted": p.name}

    @r.op("schedule.pause", ScheduleName, agent=True)
    async def schedule_pause(p: ScheduleName, caller):
        """Stop a schedule you own from ticking until it is resumed.
        `name@server` is one on a server linked with trust."""
        if (far := await far_call("schedule.pause", p, caller)) is not None:
            return far["result"]
        mine(p.name, caller, far_agent(p, caller))
        sch.pause(p.name)
        return sch.wire(p.name)

    @r.op("schedule.resume", ScheduleResume, agent=True)
    async def schedule_resume(p: ScheduleResume, caller):
        """Start a paused schedule again from now, its error count cleared. The
        ticks that fell during the pause do not fire. `name@server` is one on a
        server linked with trust; one waiting for a person stays a person's to
        unpause there too."""
        if (far := await far_call("schedule.resume", p, caller)) is not None:
            return far["result"]
        by = far_agent(p, caller)
        agent = caller.is_agent or by is not None
        s = mine(p.name, caller, by)
        person_only(p.name, agent, "unpause")
        if agent and sch.workflow_of(s.d.prompt) and not s.pin:
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
        the person approved the clock, not runs on demand. `name@server` is one
        on a server linked with trust, and its run starts there."""
        if (far := await far_call("schedule.fire", p, caller)) is not None:
            return far["result"]
        by = far_agent(p, caller)
        agent = caller.is_agent or by is not None
        s = mine(p.name, caller, by)
        person_only(p.name, agent, "fire")
        if agent and sch.workflow_of(s.d.prompt) is not None:
            raise OpError(
                "needs_person",
                f"{p.name} fires a workflow, so only its clock or a person fires it",
            )
        return {"outcome": await sch.fire_now(p.name), **sch.wire(p.name)}

    @r.op("schedule.test", ScheduleTest, agent=True)
    async def schedule_test(p: ScheduleTest, caller):
        """Try a clock and a check without saving anything: the next three
        ticks, and whether the check would fire now and why. With `name@server`
        both are tried on a server linked with trust."""
        if (
            far := await far_call("schedule.test", p, caller, speaks=False)
        ) is not None:
            return far["result"]
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
        try:
            user = app.principals.acts_for(app.who(caller).name)
        except PermissionError as e:
            raise OpError("no_linux_user", str(e)) from None
        return await sch.test(clock, p.check, where, p.name, user)
