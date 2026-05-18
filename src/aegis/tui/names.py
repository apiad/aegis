from __future__ import annotations

import random

ADJECTIVES = [
    "lucid","wry","brisk","calm","keen","sly","bold","witty","spry","sage",
    "deft","apt","nimble","quiet","amber","cobalt","fluent","hardy","jolly",
    "lithe","mellow","placid","quirky","rustic","stoic","terse","vivid",
    "zesty","arch","brave","civic","dapper","eager","frank","gilded","humble",
    "jaunty","kindly","limber","sunny",
]
LAUREATES = [
    "knuth","hopper","dijkstra","lamport","hamming","liskov","cerf","kahn",
    "diffie","hellman","rivest","shamir","adleman","karp","cook","tarjan",
    "pearl","yao","milner","backus","mccarthy","minsky","wirth","floyd",
    "hoare","thompson","ritchie","codd","engelbart","sutherland","kay",
    "perlis","newell","rabin","scott","blum","valiant","goldwasser","micali",
    "naur",
]


def generate_name(taken: set[str], rng: random.Random | None = None) -> str:
    r = rng or random
    for _ in range(1000):
        name = f"{r.choice(ADJECTIVES)}-{r.choice(LAUREATES)}"
        if name not in taken:
            return name
    # Exhaustive scan before numeric suffix fallback
    for adj in ADJECTIVES:
        for last in LAUREATES:
            name = f"{adj}-{last}"
            if name not in taken:
                return name
    base = f"{r.choice(ADJECTIVES)}-{r.choice(LAUREATES)}"
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"
