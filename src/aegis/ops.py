"""The operation registry: every action in aegis is one registered operation.

An operation has a name, a pydantic params model and an async handler that
receives the params and the caller. A websocket ``call`` is one projection of
the registry; the operations marked ``agent`` are another, as MCP tools named
after the operation with the dot as an underscore (``monitor.start`` is the tool
``monitor_start``). An agent whose session runs with ``read`` permission
calls only the operations marked ``read``: its own card, reading, and waiting
on other sessions. The rule is here, not in each harness, so it holds for every
harness and follows a live ``/permission`` change (#305). Plugins will add operations to it. Nothing reaches the client as
an action that is not an operation (DESIGN.md, "One registry, every caller").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


class OpError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        # What a client needs to act on the refusal (harness_untested's
        # version, path and command); sent beside the message, never in MCP.
        self.data = data


@dataclass(frozen=True)
class Principal:
    """Who a token is (tokens.py): a person, a linked server or a job. The
    role decides what it may change; a session's owner is a principal's name."""

    name: str
    role: str  # "admin" | "member"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class Caller:
    """Who is calling: a person through the client, or an agent through MCP.
    An agent is known by its session's log id, resolved from its spawn's
    token, never from an argument (DESIGN.md, "Caller identity is a
    transport fact")."""

    kind: str  # "user" | "agent"
    log_id: str | None = None
    # A person whose browser runs on the server's own desktop, so an action
    # like opening a file in a desktop app reaches them. Set by the transport.
    desktop: bool = False
    # A person acting through a link from another aegis server: that server's
    # name. Set by the transport from the link's hello (links.py).
    link: str | None = None
    # Who the caller's token is (tokens.py). None for an agent, whose
    # principal is its session's owner, and for the server's own calls.
    principal: Principal | None = None

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"


USER = Caller("user")

# What a schedule's run with spawn: false may not call: each starts other work
# (schedules.py). schedule.create is here so a run cannot start agents through
# a schedule of its own.
NO_SPAWN = ("session.spawn", "session.handoff", "queue.enqueue", "schedule.create")

Handler = Callable[[Any, Caller], Awaitable[Any]]


@dataclass(frozen=True)
class Operation:
    name: str
    params: type[BaseModel]
    handler: Handler
    agent: bool = False
    doc: str = ""
    # Open to an agent whose session runs with read permission.
    read: bool = False
    # Only an admin may call it (tokens.py).
    admin: bool = False
    # The param naming the session it changes: a member must own that session.
    owned: str | None = None
    # Refused to a caller acting through a link: a far server never reads or
    # writes this server's files (fs_ops.py).
    local: bool = False

    @property
    def tool_name(self) -> str:
        return self.name.replace(".", "_")


class NoParams(BaseModel):
    model_config = {"extra": "forbid"}


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}
        # A session's current permission by log id; set by the app.
        self.permission_of: Callable[[str], str | None] = lambda log_id: None
        # The principal a caller acts as, and a session's owner by log id; both
        # set by the app. Without an app every caller is an admin.
        self.who: Callable[[Caller], Principal] = lambda c: (
            c.principal or Principal("", "admin")
        )
        self.owner_of: Callable[[str], str | None] = lambda log_id: None
        # What a caller may not know exists (multi-user mode, app.py): the
        # error a missing thing gives, or None when it may see it.
        self.hides: Callable[[Principal, str, BaseModel], OpError | None] = (
            lambda who, name, params: None
        )
        # A session's schedule meta by log id (session.py); set by the app.
        self.schedule_of: Callable[[str], dict | None] = lambda log_id: None

    def op(
        self,
        name: str,
        params: type[BaseModel] = NoParams,
        *,
        agent: bool = False,
        doc: str = "",
        read: bool = False,
        admin: bool = False,
        owned: str | None = None,
        local: bool = False,
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            if name in self._ops:
                raise ValueError(f"operation {name!r} registered twice")
            self._ops[name] = Operation(
                name,
                params,
                handler,
                agent,
                doc or (handler.__doc__ or "").strip(),
                read,
                admin,
                owned,
                local,
            )
            return handler

        return register

    def names(self) -> list[str]:
        return sorted(self._ops)

    def agent_ops(self) -> list[Operation]:
        return [op for _, op in sorted(self._ops.items()) if op.agent]

    async def call(self, name: str, raw: object, caller: Caller = USER) -> Any:
        op = self._ops.get(name)
        if op is None:
            raise OpError("unknown_op", f"no operation named {name!r}")
        if caller.is_agent and not op.agent:
            raise OpError("not_for_agents", f"{name} is not open to agents")
        if (
            caller.is_agent
            and not op.read
            and self.permission_of(caller.log_id or "") == "read"
        ):
            raise OpError(
                "read_only",
                f"{op.tool_name} is not open to a session with read permission; "
                "it can call "
                + ", ".join(o.tool_name for o in self.agent_ops() if o.read),
            )
        if caller.is_agent and name in NO_SPAWN:
            run = self.schedule_of(caller.log_id or "")
            if run is not None and not run.get("spawn"):
                raise OpError(
                    "spawn_off",
                    f"run {run['run']} of {run['name']} may not start other work "
                    "(spawn: false)",
                )
        if op.local and caller.link:
            raise OpError("not_across_links", f"{name} is not open to a linked server")
        try:
            params = op.params.model_validate(raw if raw is not None else {})
        except ValidationError as e:
            raise OpError("bad_params", str(e)) from e
        who = self.who(caller)
        if op.admin and not who.is_admin:
            raise OpError("not_for_members", f"{name} is for an admin of this server")
        # Before not_owner, which would say whose it is.
        if (hidden := self.hides(who, name, params)) is not None:
            raise hidden
        if op.owned and not who.is_admin:
            target = getattr(params, op.owned, None)
            owner = self.owner_of(target) if isinstance(target, str) else None
            if owner is not None and owner != who.name:
                raise OpError(
                    "not_owner",
                    f"{target} belongs to {owner}; you can read it, not change it",
                )
        try:
            return await op.handler(params, caller)
        except PermissionError as e:
            # Nothing may act as this person (users.py, as_user.py): fail
            # closed, and say so rather than as an internal error.
            raise OpError("forbidden", str(e)) from e
