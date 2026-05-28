"""@tool decorator + global registry."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, overload

# Names that conflict with built-in aegis MCP tools.
RESERVED_NAMES = frozenset({
    "aegis_meta", "aegis_list_sessions", "aegis_list_agents",
    "aegis_handoff", "aegis_enqueue", "aegis_task_status",
    "aegis_run_workflow",
    "aegis_group_spawn", "aegis_group_broadcast", "aegis_group_wait_all",
    "aegis_group_wait_any", "aegis_group_cancel", "aegis_group_close",
    "aegis_group_list", "aegis_group_status", "aegis_group_spawn_mixed",
})

DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ToolEntry:
    name:     str
    func:     Callable[..., Any]
    timeout:  float
    qualname: str


_REGISTRY: dict[str, ToolEntry] = {}


@overload
def tool(fn: Callable) -> Callable: ...
@overload
def tool(*, name: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> Callable: ...
def tool(fn=None, *, name: str | None = None, timeout: float = DEFAULT_TIMEOUT_S):
    """Register a function as a first-class MCP tool.

    Usage:
        @tool
        async def my_tool(x: int) -> str: ...

        @tool(name="explicit", timeout=10.0)
        def sync_tool() -> str: ...
    """
    def decorate(f: Callable) -> Callable:
        n = name or f.__name__
        if n in RESERVED_NAMES:
            raise ValueError(f"tool name {n!r} is reserved by aegis")
        if n in _REGISTRY:
            raise ValueError(f"duplicate tool {n!r}")
        _REGISTRY[n] = ToolEntry(
            name=n, func=f, timeout=timeout,
            qualname=f"{f.__module__}.{f.__qualname__}",
        )
        return f
    if fn is not None:
        return decorate(fn)
    return decorate


def list_tools() -> list[ToolEntry]:
    return list(_REGISTRY.values())


def _reset_registry_for_tests() -> None:
    _REGISTRY.clear()
