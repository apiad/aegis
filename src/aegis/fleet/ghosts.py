"""Ephemeral sessions that died, kept visible for a minute.

A queue worker is spawned, runs one task and is closed
(`queue/manager.py`). On a screen you glance at, that is invisible. The
book diffs successive snapshots and holds the departed long enough to be
read.
"""

from __future__ import annotations

from dataclasses import replace

from aegis.fleet.models import CardView

GHOST_TTL = 60.0


class GhostBook:
    def __init__(self) -> None:
        self._last: dict[str, CardView] = {}
        self._ghosts: dict[str, tuple[CardView, float]] = {}

    def observe(self, cards: tuple[CardView, ...], now: float) -> None:
        live = {c.handle: c for c in cards}
        for handle, card in self._last.items():
            if handle in live or not card.origin.ephemeral:
                continue
            self._ghosts[handle] = (replace(card, ghost_since=now, tab_index=0), now)
        # A recycled handle is a live session again, never also a ghost.
        for handle in live:
            self._ghosts.pop(handle, None)
        self._last = live

    def alive(self, now: float) -> dict[str, tuple[CardView, float]]:
        self._ghosts = {h: v for h, v in self._ghosts.items() if now - v[1] < GHOST_TTL}
        return dict(self._ghosts)
