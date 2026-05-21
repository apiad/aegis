"""Agent handle generator.

Each handle is ``<adjective>-<laureate>`` where the adjective and
laureate share the same initial letter (alliteration), e.g.
``lucid-lamport``, ``keen-knuth``, ``brisk-blum``.

Within a single session (the ``taken`` set passed by the caller):

1. No laureate is reused until every laureate has appeared at least
   once. So the first N handles draw from N distinct people.
2. When a laureate must be reused (because the pool is exhausted), we
   pick an adjective whose ``adjective-laureate`` pair hasn't appeared
   yet — giving cycling variety within a person.
3. If every combination is exhausted, fall back to ``<base>-2``,
   ``<base>-3``, ….
"""
from __future__ import annotations

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

    Alliterates by construction; prefers unused laureates; cycles
    adjectives within a laureate; numeric fallback only when every
    valid alliterating pair is taken AND every laureate has been
    seen at least once.
    """
    r = rng or random

    # Laureates already drawn (regardless of which adjective).
    used_lasts = set()
    for handle in taken:
        parts = _split(handle)
        if parts is not None:
            used_lasts.add(parts[1])

    # Two-pass laureate selection: unused first, then any.
    for pool_filter in ("unused", "any"):
        if pool_filter == "unused":
            candidates = [L for L in LAUREATES if L not in used_lasts]
        else:
            candidates = list(LAUREATES)
        if not candidates:
            continue
        # Randomize the laureate order so we don't always start with 'a'.
        r.shuffle(candidates)
        for last in candidates:
            adj_pool = ADJECTIVES_BY_LETTER.get(last[0], ())
            free = [a for a in adj_pool
                    if f"{a}-{last}" not in taken]
            if free:
                return f"{r.choice(free)}-{last}"
        # No adj-laureate pair is free under this filter — try the
        # other filter, then fall through to numeric suffix.

    # Every alliterating pair is taken. Append a numeric suffix to a
    # randomly picked pair.
    last = r.choice(LAUREATES)
    adj_pool = ADJECTIVES_BY_LETTER.get(last[0], ("agent",))
    base = f"{r.choice(adj_pool)}-{last}"
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"
