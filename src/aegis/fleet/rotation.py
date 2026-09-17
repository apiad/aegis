"""What F10 shows when nobody is driving it.

Left open on a large screen, the dashboard should move to whatever just
changed and stay long enough to be read. Pure: a clock is passed in, so
the rules are tested without Textual or real time.
"""

from __future__ import annotations

from aegis.fleet.models import CardView, FleetSnapshot


def fingerprint(card: CardView) -> tuple:
    return (
        card.state,
        card.did,
        card.doing,
        card.plan_done,
        card.plan_total,
        card.plan_current,
        card.attention,
        frozenset(m.id for m in card.monitors),
    )


class Rotator:
    def __init__(
        self, *, idle_s: float = 120.0, dwell_s: float = 20.0, cycle_s: float = 30.0
    ) -> None:
        self.idle_s, self.dwell_s, self.cycle_s = idle_s, dwell_s, cycle_s
        self._touched: float | None = None
        self._cards: dict[str, CardView] = {}
        self._seen: dict[str, tuple] = {}
        self._seen_at: dict[str, float] = {}
        self._shown: str | None = None
        self._shown_since: float = float("-inf")

    def touch(self, now: float) -> None:
        self._touched = now

    def is_auto(self, now: float) -> bool:
        return self._touched is None or now - self._touched >= self.idle_s

    def observe(self, snapshot: FleetSnapshot, now: float) -> None:
        self._cards = {c.handle: c for c in snapshot.cards if c.ghost_since is None}

    def _record(self, card: CardView, now: float) -> None:
        self._seen[card.handle] = fingerprint(card)
        self._seen_at[card.handle] = now

    def _rank(self, card: CardView) -> int | None:
        # Fingerprint positions: 0 state, 1 did, 2 doing, 3-5 plan,
        # 6 attention, 7 monitor ids.
        old = self._seen.get(card.handle)
        new = fingerprint(card)
        if old == new:
            return None
        # Opening a tab acks its pending category; the ack is not news.
        if old is not None and new[6] == "" and old[:6] + old[7:] == new[:6] + new[7:]:
            return None
        if card.attention == "needs_input":
            return 0
        if card.attention == "error" or card.state == "error":
            return 1
        if old is not None and old[7] - new[7]:
            return 2
        if card.attention == "review":
            return 3
        if old is None or old[1] != new[1] or old[3:6] != new[3:6]:
            return 4
        if old[0] != new[0]:
            return 5
        return 6

    def _show(self, handle: str, now: float) -> str:
        self._record(self._cards[handle], now)
        self._shown = handle
        self._shown_since = now
        return handle

    def show(self, handle: str, now: float) -> None:
        """The operator put ``handle`` on screen, so the dwell and the
        countdown run from now, for it."""
        if handle in self._cards:
            self._show(handle, now)

    def pick(self, now: float, current: str | None) -> str | None:
        if not self._cards:
            return None
        if current not in self._cards:
            current = None
        if current is not None and now - self._shown_since < self.dwell_s:
            return current
        ranked = sorted(
            (rank, self._seen_at.get(h, float("-inf")), h)
            for h, c in self._cards.items()
            if h != current and (rank := self._rank(c)) is not None
        )
        if ranked:
            return self._show(ranked[0][2], now)
        working = sorted(h for h, c in self._cards.items() if c.state == "working")
        if working and (current is None or now - self._shown_since >= self.cycle_s):
            if current in working:
                nxt = working[(working.index(current) + 1) % len(working)]
            else:
                nxt = working[0]
            if nxt != current:
                return self._show(nxt, now)
        if current is None:
            return self._show(next(iter(self._cards)), now)
        return current

    def countdown(self, now: float) -> float | None:
        if not self.is_auto(now):
            return None
        current = self._shown if self._shown in self._cards else None
        elapsed = now - self._shown_since
        others = [c for h, c in self._cards.items() if h != current]
        if any(self._rank(c) is not None for c in others):
            return max(0.0, self.dwell_s - elapsed)
        if any(c.state == "working" for c in others):
            return max(0.0, self.cycle_s - elapsed)
        return None
