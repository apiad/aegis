"""The fleet dashboard, drawn as Rich Text.

Pure, in the shape of ``aegis.tui.sidebar.render_sidebar``: a model in, a
``Text`` out, no Textual object, no clock and no manager. Every row is cut
to the card's width here, because Textual clips an over-long line silently
and one overflowing card breaks every column of the grid.

The width floor is about 12 cells: below it the cap's fixed chrome alone
is wider than the card and a row can overflow. Nothing clamps it, because
no terminal the dashboard opens in is that narrow.
"""

from __future__ import annotations

import time

from rich.cells import cell_len
from rich.text import Text

from aegis.fleet.models import BandView, CardView, EventLine, FleetSnapshot, Origin
from aegis.tui.fit import Segment, fit, truncate_cells

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


def _fit(t: Text, parts: list[tuple[str, str]], cells: int) -> int:
    """Append ``(text, style)`` parts cut to ``cells`` in total, in order, so
    the leading parts survive and the last one to fit takes the ellipsis.
    Returns the cells used."""
    used = 0
    for text, style in parts:
        piece = truncate_cells(text, cells - used)
        if not piece:
            break
        t.append(piece, style=style)
        used += cell_len(piece)
    return used


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
    used = _fit(t, parts, inner)
    t.append(" " * max(0, inner - used), style=pal.muted)
    t.append(f" {edge}\n", style=pal.muted)


def _state_style(state: str, pal) -> str:
    return {"working": pal.working, "error": pal.error}.get(state, pal.ready)


def _cap(t: Text, card: CardView, width: int, *, fill: str, pal, style: str) -> None:
    """The top border, carrying the tab number, the handle and a state
    glyph. The only age here is the running turn's; the uptime leads the
    footer, so one number never means two things."""
    if card.ghost_since is not None:
        right = f" closed {_age(card.ghost_s)} "
    else:
        glyph = _GLYPH.get(card.state, "●")
        working = card.state == "working" and card.turn_s
        right = f" {glyph} {_age(card.turn_s)} " if working else f" {glyph} "
    tab = f"{card.tab_index} " if card.tab_index else ""
    if card.origin.ephemeral:
        tab += "⏱ "
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
    """``opus · vps · une-tools · main +3 ~2``, skipping what is unknown. The
    host is named only when it is not this machine."""
    host = "" if card.host == "local" else card.host
    return " · ".join(p for p in (card.agent_slug, host, card.repo) if p)


def _event(ev: EventLine) -> str:
    # `at` is wall-clock epoch seconds, the one stamp here that is a time
    # of day. localtime converts it; it does not read the clock.
    return f"{time.strftime('%H:%M', time.localtime(ev.at))} {ev.tool} {ev.summary}"


def _footer(card: CardView, pal) -> list[tuple[str, str]]:
    """Uptime, context, the live monitor, the two conversation edges, then
    cost and claims, as ``(text, style)`` parts split by ``·``.

    Ordered by how fast a part goes stale, because a full footer is cut from
    the right. A running ``pytest 60%`` changes every few seconds and is the
    part an operator acts on; cost and claims move slowly and the band
    repeats them. Measured: in the old order the busiest card lost exactly
    the monitor (``2 claims · pyte…``).
    """
    parts = []
    if card.uptime_s:
        parts.append((_age(card.uptime_s), pal.muted))
    if card.ctx_pct:
        style = pal.err if card.ctx_pct > 80 else pal.muted
        parts.append((f"ctx {card.ctx_pct:.0f}%", style))
    if card.monitor:
        parts.append((card.monitor, pal.muted))
    if card.spoke_with:
        parts.append((f"← {card.spoke_with[0]}", pal.muted))
    if card.waiting_on:
        parts.append((f"→ {card.waiting_on[0]}", pal.muted))
    if card.cost_usd:
        parts.append((f"${card.cost_usd:.2f}", pal.muted))
    if card.claims:
        claims = f"{card.claims} claim{'s' if card.claims != 1 else ''}"
        parts.append((claims, pal.muted))
    joined: list[tuple[str, str]] = []
    for i, part in enumerate(parts):
        if i:
            joined.append((" · ", pal.muted))
        joined.append(part)
    return joined


def render_card(card: CardView, pal, width: int, *, height: int = 0) -> Text:
    """One card. ``height`` pads it with blank bordered rows before the
    bottom border, so cards in a grid row close on the same line."""
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
    if footer := _footer(card, pal):
        row(footer)
    # The one deliberate blank: padding inside the border to the row's height.
    for _ in range(height - t.plain.count("\n") - 1):
        row("")
    t.append("└" + fill * max(0, width - 2) + "┘", style=pal.muted)
    if ghost:
        t.stylize("dim")
    return t


def columns_for(width: int) -> int:
    """Never zero — a narrow terminal gets one column, not a ZeroDivisionError."""
    return max(1, width // (CARD_WIDTH + GUTTER))


def _band(band: BandView, pal, width: int) -> Text:
    """Who is running, how they are doing, where they write, and the host
    they run on.

    The headline is ``band.total``, the live fleet. A ghost card is drawn
    but not counted, so ``len(snapshot.cards)`` would overcount.
    """
    sep = (" · ", pal.muted)
    mix = [
        (band.host, f"bold {pal.accent}"),
        sep,
        (f"{band.total} agents", f"bold {pal.ink}"),
        sep,
        (f"{band.yours} yours", pal.ink),
        sep,
        (f"{band.ephemeral} ephemeral", pal.ink),
    ]
    if band.by_kind:
        kinds = ", ".join(f"{n} {kind}" for kind, n in band.by_kind)
        mix.append((f" ({kinds})", pal.muted))
    # Summed over open sessions: it drops when a tab closes, so it is
    # labelled live and never today.
    mix += [sep, (f"${band.cost_live:.2f} live", pal.ink)]
    if band.recap_calls or band.recap_cancelled:
        recap = f"recap ${band.recap_cost:.2f} / {band.recap_calls} calls"
        if band.recap_cancelled:
            recap += f" · {band.recap_cancelled} cancelled"
        mix += [sep, (recap, pal.muted)]
    if band.clock:
        mix += [sep, (band.clock, pal.muted)]

    health = [
        (f"✻ {band.working} working", pal.working),
        sep,
        (f"● {band.ready} ready", pal.ready),
        sep,
        (f"⧗ {band.waiting} waiting", pal.muted),
        sep,
        (f"✗ {band.error} error", pal.error if band.error else pal.muted),
        sep,
        (f"ctx avg {band.ctx_avg:.0f}%", pal.muted),
    ]
    if band.ctx_worst is not None:
        handle, pct = band.ctx_worst
        health.append((f" worst {handle} {pct:.0f}%", pal.muted))
    running, configured = band.queues
    health += [sep, (f"queues {running}/{configured}", pal.muted)]
    health += [
        sep,
        (f"{band.monitors} monitor{'s' if band.monitors != 1 else ''}", pal.muted),
    ]

    t = Text()
    for parts in (mix, health):
        _fit(t, parts, width)
        t.append("\n")
    if band.repos:
        repos: list[tuple[str, str]] = [("repos ", pal.muted)]
        for i, r in enumerate(band.repos):
            if i:
                repos.append(sep)
            label = f"{r.name} ×{r.agents}" if r.agents > 1 else r.name
            if r.shared:
                # Two agents in one working tree: the collision nothing
                # else in aegis shows.
                repos.append((f"{label} ⚠", f"bold {pal.err}"))
            else:
                repos.append((label, pal.ink))
        _fit(t, repos, width)
        t.append("\n")
    # Meters first: they move every tick and the build never does, so a
    # narrow terminal sheds the build. ``fit`` skips a section with no tiers.
    row = fit(
        [
            Segment("system", band.system, 3),
            Segment("quota", band.quota, 2),
            Segment("build", band.build, 1),
        ],
        width,
        sep=" · ",
    )
    if row:
        t.append_text(Text.from_markup(row))
        t.append("\n")
    return t


def render_fleet(snapshot: FleetSnapshot, pal, width: int) -> Text:
    """The band, then the cards in rows of ``columns_for(width)``. Cards in
    a row are padded to one height so a short card does not pull the next
    row up."""
    t = _band(snapshot.band, pal, width)
    t.append("\n")
    if not snapshot.cards:
        _fit(t, [("no sessions", pal.muted)], width)
        return t
    cols = columns_for(width)
    card_w = min(CARD_WIDTH, width)
    rows = [snapshot.cards[i : i + cols] for i in range(0, len(snapshot.cards), cols)]
    for n, row in enumerate(rows):
        if n:
            t.append("\n")
        height = max(len(render_card(c, pal, card_w).split("\n")) for c in row)
        rendered = [render_card(c, pal, card_w, height=height).split("\n") for c in row]
        for y in range(height):
            for x, lines in enumerate(rendered):
                if x:
                    t.append(" " * GUTTER)
                t.append_text(lines[y])
            if y < height - 1 or n < len(rows) - 1:
                t.append("\n")
    return t
