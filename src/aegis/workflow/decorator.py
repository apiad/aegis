from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from aegis.config import ConfigError


class WorkflowError(Exception):
    """Expected failure inside a workflow (predicate violated, retry
    exhausted, etc.). Workflows raise this for clean failure reporting.
    Plain Exception is treated as an unexpected crash."""


WorkflowFn = Callable[..., Awaitable[Any]]
_REGISTRY: dict[str, WorkflowFn] = {}


def workflow(fn: WorkflowFn) -> WorkflowFn:
    """Register an async workflow under ``fn.__name__``."""
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"@workflow on {fn.__name__}: must be async def")
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if not params or params[0].name != "engine":
        raise TypeError(
            f"@workflow on {fn.__name__}: first parameter must be 'engine'")
    name = fn.__name__
    if name in _REGISTRY:
        raise ConfigError(
            f"workflow name collision: {name!r} already registered "
            f"(from {_REGISTRY[name].__module__}); cannot re-register "
            f"from {fn.__module__}")
    _REGISTRY[name] = fn
    return fn


def list_workflows() -> list[str]:
    return sorted(_REGISTRY)


def get_workflow(name: str) -> WorkflowFn | None:
    return _REGISTRY.get(name)
