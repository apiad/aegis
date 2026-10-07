"""The operation registry: every action in aegis is one registered operation.

An operation has a name, a pydantic params model and an async handler that
receives the params and the caller. A websocket ``call`` is one projection of
the registry; the operations marked ``agent`` are another, as MCP tools named
after the operation with the dot as an underscore (``monitor.start`` is the tool
``monitor_start``). Plugins will add operations to it. Nothing reaches the client as
an action that is not an operation (DESIGN.md, "One registry, every caller").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"


USER = Caller("user")

Handler = Callable[[Any, Caller], Awaitable[Any]]


@dataclass(frozen=True)
class Operation:
    name: str
    params: type[BaseModel]
    handler: Handler
    agent: bool = False
    doc: str = ""

    @property
    def tool_name(self) -> str:
        return self.name.replace(".", "_")


class NoParams(BaseModel):
    model_config = {"extra": "forbid"}


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}

    def op(
        self,
        name: str,
        params: type[BaseModel] = NoParams,
        *,
        agent: bool = False,
        doc: str = "",
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            if name in self._ops:
                raise ValueError(f"operation {name!r} registered twice")
            self._ops[name] = Operation(
                name, params, handler, agent, doc or (handler.__doc__ or "").strip()
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
        try:
            params = op.params.model_validate(raw if raw is not None else {})
        except ValidationError as e:
            raise OpError("bad_params", str(e)) from e
        return await op.handler(params, caller)
