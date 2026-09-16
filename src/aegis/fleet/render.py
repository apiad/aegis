"""The fleet dashboard, drawn as Rich Text.

Pure, in the shape of ``aegis.tui.sidebar.render_sidebar``: a model in, a
``Text`` out, no Textual object, no clock and no manager. Every row is cut
to the card's width here, because Textual clips an over-long line silently
and one overflowing card breaks every column of the grid.
"""

from __future__ import annotations

import time

from rich.cells import cell_len
from rich.text import Text

from aegis.fleet.models import CardView, EventLine, Origin
from aegis.tui.fit import truncate_cells

CARD_WIDTH = 46
GUTTER = 2
_BAR = "█"
_EMPTY = "░"
_EVENTS = 3  # the activity tail's depth
_GLYPH = {"working": "✻", "error": "✗"}  # anything else reads as ready


def _bar(done: int, total: int, cells: int = 10) -> str:
    """A progress bar that never lies about zero: 0/10 draws no full cell,
    and 10/10 draws no empty one."""
    if total <= 0:
        return ""
    filled = round(cells * done / total)
    return _BAR * filled + _EMPTY * (cells - filled)


def _age(seconds: float) -> str:
    """A duration as ``42s``, ``4m12s``, ``1h47m`` or ``2d3h``. Never a time
    of day: the values this formats are durations, not wall-clock stamps."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d{s % 86400 // 3600}h"


def _row(
    t: Text, body: str | list[tuple[str, str]], *, width: int, pal, edge: str
) -> None:
    """One bordered row, truncated to fit. Textual clips silently, so the
    truncation is ours or the grid's columns break.

    ``body`` is a string or ``(text, style)`` parts; parts are cut in order,
    so the label survives and the content takes the ellipsis.
    """
    inner = width - 4
    parts = [(body, "")] if isinstance(body, str) else body
    t.append(f"{edge} ", style=pal.muted)
    used = 0
    for text, style in parts:
        piece = truncate_cells(text, inner - used)
        if not piece:
            break
        t.append(piece, style=style)
        used += cell_len(piece)
    t.append(" " * max(0, inner - used), style=pal.muted)
    t.append(f" {edge}\n", style=pal.muted)


def _state_style(state: str, pal) -> str:
    return {"working": pal.working, "error": pal.error}.get(state, pal.ready)


def _cap(t: Text, card: CardView, width: int, *, fill: str, pal, style: str) -> None:
    """The top border, carrying the tab number, the handle and a state
    glyph with the age: the turn's while working, the session's otherwise."""
    if card.ghost_since is not None:
        # A ghost's stamp is monotonic and this renderer has no clock, so it
        # cannot be turned into an age here; say what happened instead.
        right = " closed "
    else:
        glyph = _GLYPH.get(card.state, "●")
        age = card.turn_s if card.state == "working" and card.turn_s else card.uptime_s
        right = f" {glyph} {_age(age)} "
    tab = f"{card.tab_index} " if card.tab_index else ""
    # "┌" + fill + " " ... " " + at least one fill + right + fill + "┐"
    budget = width - 4 - cell_len(tab) - 1 - cell_len(right) - 2
    if budget < 1:
        right, budget = "", width - 4 - cell_len(tab) - 1 - 2
    handle = truncate_cells(card.handle, budget)
    t.append("┌" + fill + " ", style=pal.muted)
    if tab:
        t.append(tab, style=pal.muted)
    t.append(handle, style=f"bold {style}")
    t.append(" ", style=pal.muted)
    used = 3 + cell_len(tab) + cell_len(handle) + 1 + cell_len(right) + 2
    t.append(fill * max(1, width - used), style=pal.muted)
    t.append(right, style=style)
    t.append(fill + "┐\n", style=pal.muted)


def _origin_line(origin: Origin) -> str:
    """``queue general #a3f2 → rosy-rivest``; no arrow when nothing waits."""
    head = " ".join(p for p in (origin.kind, origin.by) if p)
    if origin.detail:
        head = f"{head} #{origin.detail}"
    return f"{head} → {origin.returns_to}" if origin.returns_to else head


def _identity(card: CardView) -> str:
    """``opus · local · une-tools · main +3 ~2``, skipping what is unknown."""
    return " · ".join(p for p in (card.agent_slug, card.host, card.repo) if p)


def _event(ev: EventLine) -> str:
    # `at` is wall-clock epoch seconds, the one stamp here that is a time
    # of day. localtime converts it; it does not read the clock.
    return f"{time.strftime('%H:%M', time.localtime(ev.at))} {ev.tool} {ev.summary}"


def _footer(card: CardView) -> str:
    parts = []
    if card.ctx_pct:
        parts.append(f"ctx {card.ctx_pct:.0f}%")
    if card.cost_usd:
        parts.append(f"${card.cost_usd:.2f}")
    if card.claims:
        parts.append(f"{card.claims} claim{'s' if card.claims != 1 else ''}")
    if card.monitor:
        parts.append(card.monitor)
    if card.waiting_on:
        parts.append("waiting on " + ", ".join(card.waiting_on))
    return " · ".join(parts)


def render_card(card: CardView, pal, width: int) -> Text:
    ghost = card.ghost_since is not None
    eph = card.origin.ephemeral or ghost
    edge, fill = ("┆", "┄") if eph else ("│", "─")
    style = pal.muted if eph else _state_style(card.state, pal)
    t = Text()

    def row(body) -> None:
        _row(t, body, width=width, pal=pal, edge=edge)

    _cap(t, card, width, fill=fill, pal=pal, style=style)
    if eph:
        row([(_origin_line(card.origin), pal.accent)])
    if card.title:
        row([(card.title, f"bold {pal.ink}")])
    if identity := _identity(card):
        row([(identity, pal.muted)])
    if card.plan_total:
        plan = [
            ("plan ", pal.muted),
            (_bar(card.plan_done, card.plan_total), style),
            (f" {card.plan_done}/{card.plan_total}", pal.ink),
        ]
        if card.plan_current:
            plan.append((f" {card.plan_current}", pal.muted))
        row(plan)
    if card.did:
        row([("did ", pal.muted), (card.did, pal.ink)])
    if card.doing:
        row([("now ", pal.muted), (card.doing, pal.working)])
    for ev in card.events[-_EVENTS:]:
        row([(_event(ev), pal.muted)])
    if footer := _footer(card):
        row([(footer, pal.muted)])
    t.append("└" + fill * max(0, width - 2) + "┘", style=pal.muted)
    if ghost:
        t.stylize("dim")
    return t
