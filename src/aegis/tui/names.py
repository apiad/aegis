"""Agent handle generator.

Each handle is ``<adjective>-<laureate>`` where the adjective and
laureate share the same initial letter (alliteration), e.g.
``lucid-lamport``, ``keen-knuth``, ``brisk-blum``.

Within a session (the ``taken`` set passed by the caller) we maximize
variety along THREE axes simultaneously, prioritized in this order:

1. **Adjective freshness** — prefer adjectives never used yet, so we
   don't get back-to-back ``keen-knuth`` and ``keen-kahn``.
2. **Laureate freshness** — prefer laureates never used yet.
3. **Letter cycling** — prefer initial letters used least so far, so
   the K-block doesn't dominate just because it has the deepest pool.

When all three are tied we pick randomly among the equally-best
options. If every alliterating pair is genuinely taken we fall back
to ``<base>-2``, ``<base>-3``, ….
"""
from __future__ import annotations

import collections
import random


# All last names grouped by first letter. Every letter listed here
# MUST have a matching entry in ADJECTIVES_BY_LETTER (5+ adjectives)
# so we can always alliterate.
LAUREATES: tuple[str, ...] = (
    "adleman",
    "backus", "blum",
    "cerf", "cook", "codd",
    "diffie", "dijkstra",
    "engelbart",
    "floyd",
    "goldwasser",
    "hopper", "hamming", "hellman", "hoare",
    "knuth", "kahn", "karp", "kay",
    "lamport", "liskov",
    "milner", "mccarthy", "minsky", "micali",
    "naur", "newell",
    "pearl", "perlis",
    "rivest", "ritchie", "rabin",
    "shamir", "sutherland", "scott",
    "tarjan", "thompson",
    "valiant",
    "wirth",
    "yao",
)


# At least 5 adjectives per letter so the cycling pool is meaningful
# even when one laureate appears multiple times in a session.
ADJECTIVES_BY_LETTER: dict[str, tuple[str, ...]] = {
    "a": ("agile", "apt", "amber", "ardent", "astute", "ample"),
    "b": ("bold", "brisk", "brave", "bright", "blithe", "breezy"),
    "c": ("calm", "civic", "cozy", "candid", "crisp", "cool"),
    "d": ("deft", "dapper", "droll", "dulcet", "daring", "deep"),
    "e": ("eager", "easy", "elfin", "earnest", "eerie", "elite"),
    "f": ("fluent", "frank", "fond", "free", "fresh", "fine"),
    "g": ("gentle", "glad", "golden", "gritty", "grand", "glib"),
    "h": ("hardy", "humble", "hale", "happy", "honest", "husky"),
    "k": ("keen", "kind", "kingly", "knowing", "kindly", "knotty"),
    "l": ("lucid", "lithe", "lively", "lush", "lone", "lemon"),
    "m": ("mellow", "mild", "modest", "merry", "mighty", "mossy"),
    "n": ("nimble", "neat", "novel", "nifty", "noble", "naive"),
    "p": ("placid", "plucky", "polite", "prim", "plush", "prime"),
    "r": ("rustic", "rapid", "ready", "rosy", "regal", "roomy"),
    "s": ("stoic", "sly", "spry", "sunny", "sleek", "snug"),
    "t": ("terse", "tidy", "true", "tame", "tender", "tactful"),
    "v": ("vivid", "vibrant", "vital", "valiant", "vast", "verdant"),
    "w": ("witty", "warm", "wise", "wild", "wry", "wee"),
    "y": ("yare", "young", "yielding", "yummy", "yondly", "yeoman"),
}


# Backward-compat flat union (some tests / external code may import).
ADJECTIVES: tuple[str, ...] = tuple(
    a for pool in ADJECTIVES_BY_LETTER.values() for a in pool)


def _split(handle: str) -> tuple[str, str] | None:
    if "-" not in handle:
        return None
    adj, _, last = handle.partition("-")
    return adj, last


def generate_name(
    taken: set[str], rng: random.Random | None = None,
) -> str:
    """Return an unused ``adjective-laureate`` handle.

    Score-based: each candidate alliterating pair gets a tuple
    ``(adj_fresh, laureate_fresh, -letter_count)`` and we pick
    randomly among the lexicographic-max tier. This satisfies all
    three variety axes in priority order without needing a multi-
    pass cascade.
    """
    r = rng or random

    used_adj: set[str] = set()
    used_last: set[str] = set()
    letter_count: collections.Counter[str] = collections.Counter()
    for handle in taken:
        parts = _split(handle)
        if parts is None:
            continue
        adj, last = parts
        used_adj.add(adj)
        used_last.add(last)
        letter_count[last[0]] += 1

    # Enumerate every alliterating pair not in `taken` and score it.
    scored: list[tuple[tuple[int, int, int], str]] = []
    for last in LAUREATES:
        letter = last[0]
        for adj in ADJECTIVES_BY_LETTER.get(letter, ()):
            pair = f"{adj}-{last}"
            if pair in taken:
                continue
            score = (
                1 if adj not in used_adj else 0,    # +1 fresh adjective
                1 if last not in used_last else 0,  # +1 fresh laureate
                -letter_count[letter],              # less-used letter > more
            )
            scored.append((score, pair))

    if scored:
        max_score = max(s for s, _ in scored)
        best = [p for s, p in scored if s == max_score]
        return r.choice(best)

    # Numeric fallback — every alliterating pair AND every cycle is
    # used up. Pick a pair (random) and append the next free integer.
    last = r.choice(LAUREATES)
    adj_pool = ADJECTIVES_BY_LETTER.get(last[0], ("agent",))
    base = f"{r.choice(adj_pool)}-{last}"
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"
