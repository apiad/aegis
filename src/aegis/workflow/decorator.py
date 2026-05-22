from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from aegis.config import ConfigError


class WorkflowError(Exception):
    """Expected failure inside a workflow (predicate violated, retry
    exhausted, etc.). Workflows raise this for clean failure reporting.
    Plain Exception is treated as an unexpected crash."""


class PredicateFailed(WorkflowError):
    """`bash_predicate` retries were exhausted without a green run."""

    def __init__(self, cmd: str, result: dict, *, attempts: int) -> None:
        super().__init__(
            f"predicate failed after {attempts} attempt(s): {cmd} "
            f"(exit={result.get('exit')})")
        self.cmd = cmd
        self.result = result
        self.attempts = attempts


class SubagentSpawnError(WorkflowError):
    """Failure spawning or closing a subagent through the bridge."""


WorkflowFn = Callable[..., Awaitable[Any]]
_REGISTRY: dict[str, WorkflowFn] = {}


def _register(fn: WorkflowFn, name: str) -> WorkflowFn:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"@workflow on {fn.__name__}: must be async def")
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if not params or params[0].name != "engine":
        raise TypeError(
            f"@workflow on {fn.__name__}: first parameter must be 'engine'")
    existing = _REGISTRY.get(name)
    if existing is not None:
        # Idempotent re-registration: same source location → same workflow,
        # just the .aegis.py being reloaded. Different source location →
        # real collision.
        ec, nc = existing.__code__, fn.__code__
        if (ec.co_filename == nc.co_filename
                and ec.co_firstlineno == nc.co_firstlineno):
            _REGISTRY[name] = fn
            fn._workflow_name = name  # type: ignore[attr-defined]
            if not hasattr(fn, "_config"):
                fn._config = {}  # type: ignore[attr-defined]
            return fn
        raise ConfigError(
            f"workflow name collision: {name!r} already registered "
            f"(from {existing.__module__}); cannot re-register "
            f"from {fn.__module__}")
    _REGISTRY[name] = fn
    fn._workflow_name = name  # type: ignore[attr-defined]
    fn._config = {}  # type: ignore[attr-defined]

    def configure(**kwargs: Any) -> WorkflowFn:
        """Set per-workflow config defaults. Returned to the workflow
        body via ``engine.config`` (defaults overlaid with runtime
        kwargs in the runner)."""
        fn._config.update(kwargs)  # type: ignore[attr-defined]
        return fn

    fn.configure = configure  # type: ignore[attr-defined]
    return fn


def workflow(arg: WorkflowFn | str) -> Any:
    """Register an async workflow.

    Supports two forms::

        @workflow
        async def my_flow(engine): ...

        @workflow("custom_name")
        async def my_flow(engine): ...
    """
    if isinstance(arg, str):
        name = arg

        def deco(fn: WorkflowFn) -> WorkflowFn:
            return _register(fn, name)
        return deco
    return _register(arg, arg.__name__)


def list_workflows() -> list[str]:
    return sorted(_REGISTRY)


def get_workflow(name: str) -> WorkflowFn | None:
    return _REGISTRY.get(name)
