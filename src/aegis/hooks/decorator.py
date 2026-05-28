"""@hook decorator + per-event registry."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

VALID_EVENTS = ("pre_turn", "post_turn", "session_start", "session_end")


@dataclass(frozen=True)
class HookEntry:
    event:    str
    func:     Callable[..., Any]
    strict:   bool
    qualname: str   # for duplicate detection + log messages


_REGISTRY: dict[str, list[HookEntry]] = {ev: [] for ev in VALID_EVENTS}


def hook(event: str, *, strict: bool = False) -> Callable[[Callable], Callable]:
    """Register an async function as a hook for `event`.

    Args:
        event: one of "pre_turn", "post_turn", "session_start", "session_end".
        strict: if True, an exception raised inside this hook blocks the turn
                with the exception's string in PreTurnResult.block. Default
                False (log-and-skip; turn proceeds).
    """
    if event not in VALID_EVENTS:
        raise ValueError(
            f"unknown hook event {event!r}; valid: {VALID_EVENTS}"
        )

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        qualname = f"{fn.__module__}.{fn.__qualname__}"
        if any(e.qualname == qualname for e in _REGISTRY[event]):
            raise ValueError(f"duplicate hook {qualname!r} for {event!r}")
        _REGISTRY[event].append(
            HookEntry(event=event, func=fn, strict=strict, qualname=qualname)
        )
        return fn

    return decorate


def list_hooks(event: str | None = None) -> list[HookEntry]:
    """Return registered hooks for `event` (or all if None)."""
    if event is None:
        return [e for evs in _REGISTRY.values() for e in evs]
    return list(_REGISTRY.get(event, ()))


def _reset_registry_for_tests() -> None:
    for ev in VALID_EVENTS:
        _REGISTRY[ev].clear()
