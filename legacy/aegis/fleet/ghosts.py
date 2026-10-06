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


def _task_of(card: CardView) -> tuple[str, str, str]:
    return (card.origin.kind, card.origin.by, card.origin.detail)


class GhostBook:
    def __init__(self) -> None:
        self._last: dict[str, CardView] = {}
        self._ghosts: dict[str, tuple[CardView, float]] = {}

    def observe(self, cards: tuple[CardView, ...], now: float) -> None:
        live = {c.handle: c for c in cards}
        # A worker that renames itself departs under the old handle and
        # arrives under the new one in the same snapshot. Its origin names
        # the same task, so the old name is not a session that closed.
        # Only an arrival counts: every agent of one workflow run shares
        # its origin, and a sibling already on screen is not a rename.
        arrived = [
            _task_of(c)
            for h, c in live.items()
            if h not in self._last and c.origin.ephemeral and c.origin.detail
        ]
        for handle, card in self._last.items():
            if handle in live or not card.origin.ephemeral:
                continue
            if card.origin.detail and _task_of(card) in arrived:
                arrived.remove(_task_of(card))
                continue
            self._ghosts[handle] = (replace(card, ghost_since=now, tab_index=0), now)
        # A recycled handle is a live session again, never also a ghost.
        for handle in live:
            self._ghosts.pop(handle, None)
        self._last = live

    def alive(self, now: float) -> dict[str, tuple[CardView, float]]:
        self._ghosts = {h: v for h, v in self._ghosts.items() if now - v[1] < GHOST_TTL}
        return dict(self._ghosts)
