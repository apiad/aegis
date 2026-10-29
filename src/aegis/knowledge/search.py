"""BM25 over a principal's items, one index per kind.

Each item is one bag of words: its name split on ``-`` and ``_`` (weight 3),
its description (2) and the first 600 characters of its body (1), scored with
k1 = 1.2 and b = 0.75. Accents are folded, a short English and Spanish stop
list is dropped and words are cut to five letters, so "configuración" meets
"configuration"; "despliegue" still never meets "deploy", which is the limit
the optional embeddings address. These are the settings a spike over one
server's real knowledge measured (spec, "What the spike measured").

A query is cut to its first 2,000 characters, so a pasted log costs no more
than a prompt. Proposed and expired items are returned, marked, unless the
caller asks for what may be hinted. A memory whose ``paths:`` glob matches the
session's working directory, or one of its parents, scores double.

With the optional embeddings (embed.py), an index built with each item's
vector and asked with the query's ranks by reciprocal rank fusion: each item
scores ``1 / (60 + r)`` for its BM25 rank and again for its cosine rank, the
k = 60 of the fusion literature and of the spike. Vectors only ever add to
BM25: without the query's vector, or the items', the rank is BM25's alone.
With both, an item that shares no word with the query can still be returned
on its cosine rank, below any item both lists hold at the same ranks.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from fnmatch import fnmatch
from pathlib import PurePosixPath

from .embed import Vector, cosine
from .items import Item

K1, B = 1.2, 0.75
NAME_WEIGHT, DESCRIPTION_WEIGHT, BODY_WEIGHT = 3, 2, 1
BODY_CHARS = 600
QUERY_CHARS = 2000
FUSION_K = 60
STEM = 5
WORD = re.compile(r"[a-z0-9]+")
STOP = frozenset(
    """
    a an and are as at be but by do for from has have i if in is it its me my
    no not of on or our so that the their them then there these this to us
    was we were what when where which who why will with you your
    al como con de del el en es esa ese eso esta este esto la las lo los mas
    me mi muy no nos o para pero por que se si sin su sus te tu un una uno y ya
    """.split()
)


def terms(text: str) -> list[str]:
    """The index's words of ``text``: folded, stop words out, cut to five."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [w[:STEM] for w in WORD.findall(folded) if w not in STOP]


@dataclass(frozen=True)
class Hit:
    kind: str
    name: str
    description: str
    score: float
    status: str = "approved"
    flags: list[str] = field(default_factory=list)


def text_of(item: Item) -> str:
    """What an item's vector is made from: the same fields BM25 reads."""
    name = item.name.replace("-", " ").replace("_", " ")
    return f"{name}\n{item.description}\n{item.body[:BODY_CHARS]}"


def _bag(item: Item) -> Counter[str]:
    bag: Counter[str] = Counter()
    for weight, text in (
        (NAME_WEIGHT, item.name.replace("-", " ").replace("_", " ")),
        (DESCRIPTION_WEIGHT, item.description),
        (BODY_WEIGHT, item.body[:BODY_CHARS]),
    ):
        for t in terms(text):
            bag[t] += weight
    return bag


def _matches(globs: list[str], cwd: str) -> bool:
    here = PurePosixPath(cwd)
    places = [str(here), *(str(p) for p in here.parents)]
    for g in globs:
        g = g.rstrip("/")
        pattern = g if g.startswith(("/", "*")) else f"*/{g}"
        if any(fnmatch(p, pattern) for p in places):
            return True
    return False


class _Kind:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.bags = [_bag(i) for i in items]
        self.lens = [sum(b.values()) for b in self.bags]
        self.avg = sum(self.lens) / len(self.lens) if self.lens else 1.0
        df = Counter(t for b in self.bags for t in b)
        n = len(self.bags)
        self.idf = {t: math.log((n - d + 0.5) / (d + 0.5) + 1) for t, d in df.items()}


class Index:
    def __init__(
        self, items: list[Item], vectors: dict[str, Vector] | None = None
    ) -> None:
        self._kinds = {
            k: _Kind([i for i in items if i.kind == k]) for k in ("skill", "memory")
        }
        # Item name -> its vector, by text_of; None without embeddings.
        self.vectors = vectors

    def rank(
        self,
        query: str,
        kind: str,
        limit: int = 5,
        *,
        hintable_only: bool = False,
        cwd: str | None = None,
        query_vector: Vector | None = None,
    ) -> list[Hit]:
        """The best items of ``kind`` for ``query``, best first. Without
        vectors, an item that shares no word with the query is never
        returned; with them, BM25 and cosine ranks are fused."""
        ix = self._kinds[kind]
        q = set(terms(query[:QUERY_CHARS]))
        today = date.today()
        fuse = query_vector is not None and self.vectors is not None
        bm25: dict[str, float] = {}
        near: dict[str, float] = {}
        held: dict[str, tuple[Item, bool, bool]] = {}
        for i, (item, bag) in enumerate(zip(ix.items, ix.bags)):
            expired = item.expired(today)
            if hintable_only and (expired or item.status != "approved"):
                continue
            norm = K1 * (1 - B + B * ix.lens[i] / ix.avg)
            score = sum(
                ix.idf[t] * bag[t] * (K1 + 1) / (bag[t] + norm) for t in q if t in bag
            )
            boosted = bool(cwd and item.paths and _matches(item.paths, cwd))
            if score > 0:
                bm25[item.name] = score
            v = (self.vectors or {}).get(item.name) if fuse else None
            if v is not None and query_vector is not None:
                near[item.name] = cosine(query_vector, v)
            if item.name in bm25 or item.name in near:
                held[item.name] = (item, expired, boosted)
        final = bm25
        if near:
            final = {}
            for ranked in (bm25, near):
                order = sorted(ranked, key=lambda n: (-ranked[n], n))
                for r, name in enumerate(order, 1):
                    final[name] = final.get(name, 0.0) + 1 / (FUSION_K + r)
        scored: list[tuple[float, str, Hit]] = []
        for name, score in final.items():
            item, expired, boosted = held[name]
            score = score * 2 if boosted else score
            flags = ["expired"] if expired else []
            hit = Hit(kind, item.name, item.description, score, item.status, flags)
            scored.append((-score, item.name, hit))
        scored.sort(key=lambda s: (s[0], s[1]))
        return [h for _, _, h in scored[:limit]]
