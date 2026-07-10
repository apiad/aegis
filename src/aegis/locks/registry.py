from __future__ import annotations

from typing import Callable

from aegis.locks.models import Claim, claims_overlap
from aegis.queue.schema import new_ulid, now_iso


class ClaimRegistry:
    def __init__(self,
                 live_handles: Callable[[], set[str]] | None = None,
                 log=None) -> None:
        self._claims: dict[str, Claim] = {}
        self._live = live_handles or (lambda: set())
        self._log = log

    def _prune_dead(self) -> None:
        live = self._live()
        dead = [cid for cid, c in self._claims.items() if c.handle not in live]
        for cid in dead:
            c = self._claims.pop(cid)
            if self._log is not None:
                self._log.write(self._log.reaped(cid, c.handle, now_iso()))

    def claim(self, handle: str, prefixes, files,
              intent: str = "shared",
              desc: str = "") -> tuple[Claim, bool, list[Claim]]:
        self._prune_dead()
        candidate = Claim(claim_id=new_ulid(), handle=handle,
                          prefixes=frozenset(prefixes), files=frozenset(files),
                          intent=intent, desc=desc, since=now_iso())
        overlaps = [c for c in self._claims.values()
                    if c.handle != handle and claims_overlap(candidate, c)]
        if intent == "exclusive":
            granted = len(overlaps) == 0
        else:  # shared
            granted = not any(c.intent == "exclusive" for c in overlaps)
        if granted:
            self._claims[candidate.claim_id] = candidate
            if self._log is not None:
                self._log.write(self._log.claimed(candidate))
        return candidate, granted, overlaps

    def release(self, claim_id: str, handle: str) -> bool:
        c = self._claims.get(claim_id)
        if c is None or c.handle != handle:
            return False
        del self._claims[claim_id]
        if self._log is not None:
            self._log.write(self._log.released(claim_id, handle, now_iso()))
        return True

    def active(self) -> list[Claim]:
        self._prune_dead()
        return list(self._claims.values())

    def reap(self, handle: str) -> None:
        gone = [cid for cid, c in self._claims.items() if c.handle == handle]
        for cid in gone:
            self._claims.pop(cid)
            if self._log is not None:
                self._log.write(self._log.reaped(cid, handle, now_iso()))
