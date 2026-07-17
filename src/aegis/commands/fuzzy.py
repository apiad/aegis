"""Pure fuzzy subsequence matcher for the command palette. Case-insensitive;
scores contiguity and start-of-word matches higher; returns matched positions
so the UI can bold them. No registry, no bridge, no UI."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def fuzzy_match(query: str, candidate: str) -> tuple[float, tuple[int, ...]] | None:
    """Return (score, matched_indices) if every char of ``query`` appears in
    ``candidate`` in order (case-insensitive), else None. Empty query matches
    with score 0.0 and no positions."""
    if not query:
        return 0.0, ()
    q = query.lower()
    c = candidate.lower()
    positions: list[int] = []
    score = 0.0
    ci = 0
    prev = -2
    for ch in q:
        idx = c.find(ch, ci)
        if idx == -1:
            return None
        if idx == prev + 1:
            score += 2.0                      # contiguous run
        if idx == 0 or not c[idx - 1].isalnum():
            score += 3.0                      # start-of-word
        positions.append(idx)
        prev = idx
        ci = idx + 1
    score -= len(candidate) * 0.01            # prefer shorter candidates
    return score, tuple(positions)


def fuzzy_rank(query: str, items: list, key: Callable[[Any], str] = lambda x: x) -> list:
    """Keep items whose ``key`` fuzzy-matches ``query``, sorted by score desc.
    Stable: equal scores preserve input order."""
    scored: list[tuple[float, int, Any]] = []
    for i, item in enumerate(items):
        m = fuzzy_match(query, key(item))
        if m is not None:
            scored.append((m[0], i, item))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]
