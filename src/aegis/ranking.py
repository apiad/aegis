"""How good each model is and what it costs, from the vendored ``data/models.json``.

The scores are Artificial Analysis's Coding and Intelligence Indexes, and the
price is its list price in USD per million tokens. ``scripts/refresh-model-ranking``
rebuilds the file before a release; aegis never calls Artificial Analysis at
run time, so a model released since the last refresh is unranked.

The file is keyed by the model id an agent names (the catalog's ``value``).
A model is ranked when it has an Intelligence Index. Ranked models order by
Intelligence Index, higher first, the Coding Index breaking ties; unranked ones
go after every ranked one, in the order they were given. The Intelligence Index
orders because Artificial Analysis publishes it for every model it lists, while
many, the newest included, have no Coding Index.

``AEGIS_MODELS_FILE`` names a file read in place of the packaged one, so a
test can serve sample scores from a real ``aegis serve``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cache
from importlib import resources
from pathlib import Path

from .claude.control import Model


@dataclass(frozen=True)
class Score:
    aa: str  # the Artificial Analysis slug
    coding: float | None
    intelligence: float | None
    price_in: float | None
    price_out: float | None


@dataclass(frozen=True)
class Ranking:
    refreshed: str  # YYYY-MM-DD, or "" before the first refresh
    models: dict[str, Score]
    url: str = "https://artificialanalysis.ai"  # the attribution's link

    @classmethod
    def from_dict(cls, data: dict) -> Ranking:
        return cls(
            refreshed=data.get("refreshed") or "",
            url=data.get("url") or "https://artificialanalysis.ai",
            models={
                k: Score(
                    aa=v.get("aa", ""),
                    coding=v.get("coding"),
                    intelligence=v.get("intelligence"),
                    price_in=v.get("price_in"),
                    price_out=v.get("price_out"),
                )
                for k, v in (data.get("models") or {}).items()
            },
        )

    def score(self, model: str) -> Score | None:
        return self.models.get(model)

    def _key(self, model: str) -> tuple[float, float] | None:
        s = self.models.get(model)
        if s is None or s.intelligence is None:
            return None
        return (s.intelligence, s.coding if s.coding is not None else -1.0)

    def order(self, models: Sequence[str]) -> list[str]:
        """The models by Intelligence Index, higher first, the Coding Index
        breaking ties; those with no Intelligence Index last, as given."""
        ranked = [m for m in models if self._key(m) is not None]
        ranked.sort(key=lambda m: self._key(m) or (0.0, 0.0), reverse=True)
        return ranked + [m for m in models if self._key(m) is None]

    def rank(self, model: str, among: Sequence[str]) -> int | None:
        """The model's 1-based place in ``order(among)``, or None when it is
        not among them or has no Intelligence Index."""
        if model not in among or self._key(model) is None:
            return None
        return self.order(among).index(model) + 1


@cache
def ranking() -> Ranking:
    path = os.environ.get("AEGIS_MODELS_FILE")
    text = (
        Path(path).read_text()
        if path
        else resources.files("aegis").joinpath("data/models.json").read_text()
    )
    return Ranking.from_dict(json.loads(text))


def annotate(models: Sequence[Model], r: Ranking | None = None) -> tuple[Model, ...]:
    """The models with their scores and rank among themselves, and the file's
    price where the harness gave none. Pass one harness's catalog: the rank
    is among the models given."""
    r = r or ranking()
    among = [m.value for m in models]
    out = []
    for m in models:
        s = r.score(m.value)
        if s is None:
            out.append(m)
            continue
        own = m.price_in is not None or m.price_out is not None
        out.append(
            replace(
                m,
                rank=r.rank(m.value, among),
                coding=s.coding,
                intelligence=s.intelligence,
                price_in=m.price_in if own else s.price_in,
                price_out=m.price_out if own else s.price_out,
            )
        )
    return tuple(out)
