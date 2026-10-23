"""``Knowledge``: what the ops and the send hook call.

It owns the store, one cached index per principal (rebuilt when the store's
version for that principal moves), the usage log, and the ranks of the last
hint each session got, so a read that follows a hint records the rank it
came from. The usage log is pruned at most once a day, on a hint.
"""

from __future__ import annotations

import time
from pathlib import Path

from aegis.knowledge import hint
from aegis.knowledge.search import Index
from aegis.knowledge.store import Store
from aegis.knowledge.usage import Usage

HINT_ITEMS = 3
PRUNE_EVERY_S = 86400


class Knowledge:
    def __init__(self, state_root: Path) -> None:
        self.store = Store(state_root / "knowledge")
        self.usage = Usage(state_root / "knowledge-usage.sqlite")
        self._indexes: dict[str, tuple[int, Index]] = {}
        self._ranks: dict[str, dict[str, int]] = {}
        self._pruned = 0.0

    def index(self, principal: str) -> Index:
        version = self.store.version(principal)
        held = self._indexes.get(principal)
        if held is None or held[0] != version:
            held = (version, Index(self.store.items(principal)))
            self._indexes[principal] = held
        return held[1]

    def hint_for(
        self, principal: str, session_id: str, text: str, cwd: str | None
    ) -> str:
        """The hint block for a send of ``text``, or "" when nothing matches.
        Writes one ``hinted`` row per item shown, with its rank."""
        now = time.time()
        if now - self._pruned > PRUNE_EVERY_S:
            self._pruned = now
            self.usage.prune(now)
        ix = self.index(principal)
        skills = ix.rank(text, "skill", HINT_ITEMS, hintable_only=True, cwd=cwd)
        memories = ix.rank(text, "memory", HINT_ITEMS, hintable_only=True, cwd=cwd)
        ranks: dict[str, int] = {}
        for hits in (skills, memories):
            for rank, h in enumerate(hits, 1):
                ranks[h.name] = rank
                self.usage.add(
                    principal, session_id, h.kind, h.name, "hinted", f"rank={rank}"
                )
        self._ranks[session_id] = ranks
        return hint.block(skills, memories)

    def read_rank(self, session_id: str, name: str) -> int | None:
        """The rank ``name`` had in the last hint ``session_id`` got."""
        return self._ranks.get(session_id, {}).get(name)

    def forget(self, session_id: str) -> None:
        self._ranks.pop(session_id, None)
