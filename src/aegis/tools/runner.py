"""Tool invocation wrapper with timeout + JSONL logging."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any

from aegis.tools.decorator import ToolEntry


class ToolTimeout(TimeoutError):
    """Raised when a tool exceeds its declared timeout."""


async def invoke_tool(
    entry: ToolEntry,
    *,
    kwargs: dict[str, Any],
    state_dir: Path,
) -> Any:
    """Invoke a tool, enforcing timeout and writing a JSONL log line.

    Sync functions are wrapped in a default executor so the timeout
    semantics still apply.
    """
    log_path = state_dir / "tools" / f"{entry.name}.jsonl"
    started = time.time()

    async def _call():
        if inspect.iscoroutinefunction(entry.func):
            return await entry.func(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: entry.func(**kwargs))

    try:
        result = await asyncio.wait_for(_call(), timeout=entry.timeout)
        _log(log_path, status="ok", entry=entry, started=started, kwargs=kwargs)
        return result
    except asyncio.TimeoutError:
        _log(log_path, status="timeout", entry=entry, started=started, kwargs=kwargs)
        raise ToolTimeout(f"tool {entry.name!r} exceeded {entry.timeout}s")
    except Exception as exc:
        _log(log_path, status="exception", entry=entry, started=started,
             kwargs=kwargs, error=f"{type(exc).__name__}: {exc}")
        raise


def _log(
    path: Path, *, status: str, entry: ToolEntry, started: float,
    kwargs: dict[str, Any], error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(), "duration": time.time() - started,
        "tool": entry.name, "qualname": entry.qualname,
        "status": status, "kwargs": _safe_repr(kwargs),
    }
    if error is not None:
        rec["error"] = error
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def _safe_repr(kwargs: dict[str, Any]) -> dict[str, str]:
    return {k: repr(v)[:200] for k, v in kwargs.items()}
