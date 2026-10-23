"""``Knowledge``: what the ops and the send hook call.

It owns the store, one cached index per principal, the usage log, and the
ranks of the last hint each session got, so a read that follows a hint
records the rank it came from. The index is rebuilt in a thread, off the
event loop, when the store's version for that principal moves (a change
through aegis) or when a stat scan, at most every ``SCAN_EVERY_S`` per
principal, finds a file changed by hand; the scan parses only changed files
(store.py). The usage log is pruned at most once a day, on a hint.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from . import hint
from .items import Item
from .search import Index
from .store import Store
from .usage import Usage

HINT_ITEMS = 3
PRUNE_EVERY_S = 86400
SCAN_EVERY_S = 5.0


class Knowledge:
    def __init__(self, state_root: Path) -> None:
        self.store = Store(state_root / "knowledge")
        self.usage = Usage(state_root / "knowledge-usage.sqlite")
        # principal -> (store version, when scanned, items, index)
        self._indexes: dict[str, tuple[int, float, list[Item], Index]] = {}
        self._ranks: dict[str, dict[str, int]] = {}
        self._pruned = 0.0
        self._refreshing: dict[str, asyncio.Task] = {}
        self._clock = time.monotonic

    def _rebuild(self, principal: str, old) -> tuple[list[Item], Index]:
        items, changed = self.store.scan(principal)
        if old is not None and not changed:
            return old[2], old[3]
        return items, Index(items)

    def _fresh(self, principal: str, held) -> bool:
        return (
            held is not None
            and held[0] == self.store.version(principal)
            and self._clock() - held[1] < SCAN_EVERY_S
        )

    async def refresh(self, principal: str) -> tuple[list[Item], Index]:
        """Rebuild ``principal``'s index in a thread if it is stale."""
        held = self._indexes.get(principal)
        if held is not None and self._fresh(principal, held):
            return held[2], held[3]
        version, now = self.store.version(principal), self._clock()
        items, ix = await asyncio.to_thread(self._rebuild, principal, held)
        self._indexes[principal] = (version, now, items, ix)
        return items, ix

    def _now_index(self, principal: str) -> Index:
        """The index a send ranks against, without yielding: a send must not
        give the loop a turn before it is recorded, or two sends could be
        recorded out of order. A stale index is used as it is while a
        refresh runs in a thread; only a principal with no index yet is
        built here, once per server start."""
        held = self._indexes.get(principal)
        if held is None:
            version, now = self.store.version(principal), self._clock()
            items, ix = self._rebuild(principal, None)
            self._indexes[principal] = (version, now, items, ix)
            return ix
        if not self._fresh(principal, held) and principal not in self._refreshing:
            try:
                task = asyncio.get_running_loop().create_task(self.refresh(principal))
            except RuntimeError:  # no loop: a plain call, as in a unit test
                self._indexes[principal] = (
                    self.store.version(principal),
                    self._clock(),
                    *self._rebuild(principal, held),
                )
                return self._indexes[principal][3]
            self._refreshing[principal] = task
            task.add_done_callback(lambda _t: self._refreshing.pop(principal, None))
        return held[3]

    async def _held(self, principal: str) -> tuple[list[Item], Index]:
        return await self.refresh(principal)

    async def index(self, principal: str) -> Index:
        return (await self._held(principal))[1]

    async def items(self, principal: str) -> list[Item]:
        """Every item of ``principal``, from the cache the index uses."""
        return (await self._held(principal))[0]

    def hint_for(
        self, principal: str, session_id: str, text: str, cwd: str | None
    ) -> str:
        """The hint block for a send of ``text``, or "" when nothing matches.
        Writes one ``hinted`` row per item shown, with its rank."""
        now = time.time()
        if now - self._pruned > PRUNE_EVERY_S:
            self._pruned = now
            self.usage.prune(now)
        ix = self._now_index(principal)
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
