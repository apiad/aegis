"""The operation registry: every action in aegis2 is one registered operation.

An operation has a name, a pydantic params model and an async handler. A
websocket ``call`` is one projection of the registry; MCP tools will be
another, and plugins will add operations to it. Nothing reaches the client as
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


Handler = Callable[[Any], Awaitable[Any]]


@dataclass(frozen=True)
class Operation:
    name: str
    params: type[BaseModel]
    handler: Handler


class NoParams(BaseModel):
    model_config = {"extra": "forbid"}


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}

    def op(
        self, name: str, params: type[BaseModel] = NoParams
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            if name in self._ops:
                raise ValueError(f"operation {name!r} registered twice")
            self._ops[name] = Operation(name, params, handler)
            return handler

        return register

    def names(self) -> list[str]:
        return sorted(self._ops)

    async def call(self, name: str, raw: object) -> Any:
        op = self._ops.get(name)
        if op is None:
            raise OpError("unknown_op", f"no operation named {name!r}")
        try:
            params = op.params.model_validate(raw if raw is not None else {})
        except ValidationError as e:
            raise OpError("bad_params", str(e)) from e
        return await op.handler(params)
