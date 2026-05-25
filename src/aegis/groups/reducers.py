"""Named reducers for ``GroupResult.combined``."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aegis.groups.models import MemberResult

Reducer = Callable[[dict[str, MemberResult], list[str]], Any]


def concat(by_member: dict[str, MemberResult], order: list[str]) -> str:
    parts = []
    for handle in order:
        mr = by_member.get(handle)
        if mr is None:
            continue
        parts.append(f"---\n{handle}: {mr.text}")
    return "\n\n".join(parts)


_REGISTRY: dict[str, Reducer] = {
    "concat": concat,
}


def get_reducer(name: str) -> Reducer:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown reducer {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def register_reducer(name: str, fn: Reducer) -> None:
    _REGISTRY[name] = fn
