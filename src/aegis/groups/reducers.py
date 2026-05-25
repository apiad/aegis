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


def join_by_handle(by_member: dict[str, MemberResult],
                   order: list[str]) -> dict[str, str]:
    return {h: by_member[h].text for h in order if h in by_member}


def last_wins(by_member: dict[str, MemberResult],
              order: list[str]) -> str:
    if not order:
        return ""
    return by_member[order[-1]].text


def majority_vote(by_member: dict[str, MemberResult],
                  order: list[str]) -> str:
    from collections import Counter
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for i, h in enumerate(order):
        text = by_member[h].text.strip()
        counts[text] += 1
        first_seen.setdefault(text, i)
    if not counts:
        return ""
    top = max(counts.items(),
              key=lambda kv: (kv[1], -first_seen[kv[0]]))
    return top[0]


register_reducer("join_by_handle", join_by_handle)
register_reducer("last_wins", last_wins)
register_reducer("majority_vote", majority_vote)
