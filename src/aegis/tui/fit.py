"""Width-aware composition for the status bar.

Textual clips an over-long status line silently, so the right-hand segments
vanish without a trace. ``fit`` degrades segments by priority until the line
fits: the lowest-priority segment that can still narrow does so, and a segment
already at its narrowest is dropped. Pure — no Textual import — so it is
testable as a plain function.

Segments are rendered in *list* order and degraded in *priority* order; the two
are deliberately independent, so visual layout does not dictate what survives a
narrow terminal.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_MARKUP = re.compile(r"\[[^\]]*\]")


def strip_markup(text: str) -> str:
    """``text`` with Rich markup tags removed."""
    return _MARKUP.sub("", text)


def plain_width(text: str) -> int:
    """Visible width of ``text``, ignoring Rich markup tags.

    Glyphs used in the bar (↑ ↓ ⚒ ⚡ ⧗ ⟳ ✻) are single-width in the terminals
    aegis targets, so a character count is accurate enough; no wcwidth
    dependency is pulled in for the general case.
    """
    return len(strip_markup(text))


@dataclass(frozen=True)
class Segment:
    """One status-bar segment.

    ``tiers`` runs widest-first; the last entry is the narrowest form the
    segment can take before being dropped entirely. ``priority`` is compared
    across segments — higher survives longer.
    """
    key: str
    tiers: tuple[str, ...]
    priority: int


def fit(segments: Sequence[Segment], width: int, sep: str = "    ") -> str:
    """Compose ``segments`` into a line no wider than ``width``.

    ``width <= 0`` means "unmeasured" (the widget has not been laid out yet)
    and renders every segment at tier 0.
    """
    live = [(s, 0) for s in segments if s.tiers]

    def render(items: list[tuple[Segment, int]]) -> str:
        return sep.join(s.tiers[i] for s, i in items if s.tiers[i])

    if width <= 0:
        return render(live)

    while live and plain_width(render(live)) > width:
        # Exhaust the lowest-priority segment — every tier, then drop it —
        # before touching anything above it. Degrading a higher-priority
        # segment while a lower one still has room to give would invert the
        # ranking. Ties broken by position so the pass is stable.
        order = sorted(range(len(live)), key=lambda k: (live[k][0].priority, k))
        k = order[0]
        seg, tier = live[k]
        if tier + 1 < len(seg.tiers):
            live[k] = (seg, tier + 1)
        elif len(live) > 1:
            live.pop(k)
        else:
            break

    out = render(live)
    if plain_width(out) > width:
        # One segment left and still too wide: hard-truncate the plain text.
        out = strip_markup(out)[:width]
    return out


def fit_rows(segments: Sequence[Segment], width: int) -> list[str]:
    """Compose ``segments`` one per row, each at the widest tier that fits.

    The vertical counterpart of ``fit``, for the sidebar column. Because
    rows do not share horizontal space, segments do not compete and
    ``priority`` is never consulted — each is considered on its own. That
    is the whole difference: ``fit`` decides what to *sacrifice*, this
    decides only how narrow each thing gets to be.

    A segment whose *narrowest* tier still overflows is dropped rather than
    truncated: half a number reads as a number, which is worse than no row.

    ``width <= 0`` means "unmeasured" (the widget has not been laid out
    yet) and renders every segment at tier 0, matching ``fit``.
    """
    rows: list[str] = []
    for seg in segments:
        tiers = [t for t in seg.tiers if t]
        if not tiers:
            continue
        if width <= 0:
            rows.append(tiers[0])
            continue
        pick = next((t for t in tiers if plain_width(t) <= width), None)
        if pick is not None:
            rows.append(pick)
    return rows
