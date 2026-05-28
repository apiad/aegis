"""Hook invocation with timeout, exception handling, and JSONL logging."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from aegis.hooks.composer import compose_pre_turn
from aegis.hooks.contexts import (
    PostTurnEvent, PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionStartEvent,
)
from aegis.hooks.decorator import HookEntry

DEFAULT_TIMEOUT_S = 5.0


async def run_pre_turn_hooks(
    ctx: PreTurnContext,
    entries: list[HookEntry],
    *,
    state_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> PreTurnResult:
    """Run all pre_turn hooks in declaration order, composing the result.

    Each hook sees `ctx.prior_results` populated with results from earlier
    hooks of this turn. Per-hook exceptions/timeouts are logged to
    `state_dir/hooks/<qualname>.jsonl`. Strict hooks turn an exception
    into a `block` result; non-strict hooks log-and-skip.
    """
    results: list[PreTurnResult] = []
    for entry in entries:
        ctx_for_hook = PreTurnContext(
            session=ctx.session,
            user_message=ctx.user_message,
            history=ctx.history,
            project_root=ctx.project_root,
            prior_results=tuple(results),
        )
        result = await _invoke_with_timeout(
            entry, ctx_for_hook, state_dir=state_dir, timeout=timeout,
        )
        if result is not None:
            results.append(result)
    return compose_pre_turn(results)


async def run_observer_hooks(
    event: PostTurnEvent | SessionStartEvent | SessionEndEvent,
    entries: list[HookEntry],
    *,
    state_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Fire every observer hook for an event. Return value ignored."""
    for entry in entries:
        await _invoke_with_timeout(
            entry, event, state_dir=state_dir, timeout=timeout,
        )


async def _invoke_with_timeout(
    entry: HookEntry,
    payload: Any,
    *,
    state_dir: Path,
    timeout: float,
) -> PreTurnResult | None:
    """Invoke `entry.func(payload)` with timeout + JSONL logging.

    Returns the function's return value on success; None on timeout/exception.
    For strict pre_turn hooks, exceptions are converted to
    PreTurnResult(block=str(exc)) and returned.
    """
    log_path = state_dir / "hooks" / f"{entry.qualname}.jsonl"
    started = time.time()
    try:
        result = await asyncio.wait_for(entry.func(payload), timeout=timeout)
        _log(log_path, status="ok", entry=entry, started=started)
        return result
    except asyncio.TimeoutError:
        _log(log_path, status="timeout", entry=entry, started=started)
        return None
    except Exception as exc:  # noqa: BLE001 — log + skip semantics
        _log(log_path, status="exception", entry=entry, started=started,
             error=f"{type(exc).__name__}: {exc}")
        if entry.strict and entry.event == "pre_turn":
            return PreTurnResult(
                block=f"strict hook {entry.qualname} raised: {exc}"
            )
        return None


def _log(
    path: Path,
    *,
    status: str,
    entry: HookEntry,
    started: float,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts":       time.time(),
        "duration": time.time() - started,
        "event":    entry.event,
        "qualname": entry.qualname,
        "strict":   entry.strict,
        "status":   status,
    }
    if error is not None:
        rec["error"] = error
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
