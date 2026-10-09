"""Session handles and default titles.

A handle is ``<adjective>-<laureate>`` with a shared initial (``lucid-lamport``),
unique among every session on the server, archived ones included. It is a label a
person reads and renames; the log id is the key (DESIGN.md). The word lists are
copied from the old tree's handle pool.
"""

from __future__ import annotations

import random
import re

LAUREATES = (
    "adleman backus blum cerf cook codd diffie dijkstra engelbart floyd goldwasser "
    "hopper hamming hellman hoare knuth kahn karp kay lamport liskov milner mccarthy "
    "minsky micali naur newell pearl perlis rivest ritchie rabin shamir sutherland "
    "scott tarjan thompson valiant wirth yao"
).split()

ADJECTIVES_BY_LETTER = {
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

PAIRS = tuple(
    f"{adj}-{last}" for last in LAUREATES for adj in ADJECTIVES_BY_LETTER[last[0]]
)

_HANDLE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+){1,2}$")
TITLE_DEFAULT_MAX = 60
TITLE_MAX = 120


def valid_handle(h: str) -> bool:
    """2 or 3 lowercase alphanumeric segments joined by hyphens, starting with a letter."""
    return bool(_HANDLE.match(h))


def mint_handle(taken: set[str], rng: random.Random | None = None) -> str:
    r = rng or random
    free = [p for p in PAIRS if p not in taken]
    if free:
        return r.choice(free)
    base = r.choice(PAIRS)
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


# The first and last markers of a /spawn opening (agent_ops.spawn_opening), here
# so a rewording cannot stop the title from finding the task: every spawned tab
# would be titled with the same preamble.
SPAWN_HEAD = "The person started you from inside another conversation, tab "
SPAWN_TASK = "\n\nThe person's task: "


def default_title(text: str) -> str:
    """The first non-empty line of a prompt, cut at 60 characters; of a /spawn
    opening, of the task the person typed, which comes last."""
    if text.startswith(SPAWN_HEAD) and SPAWN_TASK in text:
        text = text.rsplit(SPAWN_TASK, 1)[1]
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return (
        line if len(line) <= TITLE_DEFAULT_MAX else line[: TITLE_DEFAULT_MAX - 1] + "…"
    )
