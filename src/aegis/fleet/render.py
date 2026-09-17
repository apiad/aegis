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
_COUNTERS = (
    ("needs_input", "need you"),
    ("error", "error"),
    ("review", "review"),
    ("waiting", "waiting"),
    ("done", "done"),
)


def bar(pct: float, cells: int, style: str, pal) -> Text:
    cells = max(0, cells)
    filled = round(cells * max(0.0, min(100.0, pct)) / 100)
    t = Text(_BAR * filled, style=style)
    t.append(_EMPTY * (cells - filled), style=pal.rule)
    return t


def sweep_bar(cells: int, frame: int, pal) -> Text:
    """An indeterminate bar: a block that moves one cell per frame."""
    cells = max(1, cells)
    block = max(1, cells // 4)
    start = frame % cells
    lit = {(start + i) % cells for i in range(block)}
    t = Text()
    for i in range(cells):
        t.append(
            _BAR if i in lit else _EMPTY, style=pal.accent if i in lit else pal.rule
        )
    return t


def pulse(style: str, frame: int, pal) -> str:
    return style if frame % 2 == 0 else pal.muted


def blink(text: str, frame: int) -> str:
    return text if frame % 2 == 0 else " " * cell_len(text)


def severity_style(severity: str, pal) -> str:
    return {"warning": pal.accent, "critical": pal.error}.get(severity, pal.ready)


def ctx_style(pct: float, pal) -> str:
    if pct > 80:
        return pal.error
    if pct > 60:
        return pal.accent
    return pal.ready


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


def _one_line(text: str) -> str:
    """``text`` with every control character turned into a space.

    Session text is untrusted for layout: a tool summary like
    ``python3 - <<'PY'`` followed by a newline split one card row across two
    terminal lines on the operator's screen (2026-09-16), shifting every
    card to its right. Measuring cells cannot catch that; a newline is zero
    cells wide.
    """
    return "".join(" " if ch < " " or ch == "\x7f" else ch for ch in text)


def _fit(t: Text, parts: list[tuple[str, str]], cells: int) -> int:
    """Append ``(text, style)`` parts cut to ``cells`` in total, in order, so
    the leading parts survive and the last one to fit takes the ellipsis.
    Returns the cells used."""
    used = 0
    for text, style in parts:
        piece = truncate_cells(_one_line(text), cells - used)
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
    # The recap says what the session did; the raw commands are only a
    # fallback for a card that has no recap yet (operator ruling 2026-09-16).
    if not (card.did or card.doing):
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
        noun = "call" if band.recap_calls == 1 else "calls"
        recap = f"recap ${band.recap_cost:.2f} / {band.recap_calls} {noun}"
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


def _gauge(
    label: str,
    pct: float,
    value: str,
    style: str,
    cells: int,
    pal,
    *,
    value_style: str | None = None,
    tail: str = "",
) -> Text:
    """``label bar value tail`` in ``cells`` cells or fewer, never more."""
    head = f"{label} ".ljust(6)
    rest = f" {value}" + (f" {tail}" if tail else "")
    bar_cells = max(3, cells - cell_len(head) - cell_len(rest) - 1)
    t = Text(head, style=pal.muted)
    t.append_text(bar(pct, bar_cells, style, pal))
    t.append(f" {value}", style=value_style or pal.ink)
    if tail:
        t.append(f" {tail}", style=pal.muted)
    t.truncate(cells)
    return t


def _rows_of(gauges: list[Text], per_row: int) -> Text:
    t = Text()
    for i in range(0, len(gauges), per_row):
        if i:
            t.append("\n")
        for j, g in enumerate(gauges[i : i + per_row]):
            if j:
                t.append("  ")
            t.append_text(g)
    return t


def _reset(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s >= 3600:
        return f"↻ {s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"↻ {s // 60}m"
    return f"↻ {s}s"


def render_band(snapshot: FleetSnapshot, pal, width: int, frame: int) -> Text:
    """The v2 band: host gauges, quota gauges (when any), then the attention
    counters and totals. Under 110 columns the gauges go two per line."""
    from aegis.attention import GLYPHS, mark, style_for

    band = snapshot.band
    narrow = width < 110
    t = Text()

    per = 2 if narrow else 4
    cells = (width - 2 * (per - 1)) // per
    host: list[Text] = []
    if band.stats is not None:
        s = band.stats
        ram_tail = (
            f"{s.ram_used_gb:.1f}/{s.ram_total_gb:.0f}G" if s.ram_total_gb else ""
        )
        host += [
            _gauge("CPU", s.cpu, f"{s.cpu:.0f}%", ctx_style(s.cpu, pal), cells, pal),
            _gauge(
                "RAM",
                s.ram,
                f"{s.ram:.0f}%",
                ctx_style(s.ram, pal),
                cells,
                pal,
                tail=ram_tail,
            ),
            _gauge("DSK", s.disk, f"{s.disk:.0f}%", ctx_style(s.disk, pal), cells, pal),
        ]
    host.append(
        _gauge(
            "CTX",
            band.ctx_avg,
            f"{band.ctx_avg:.0f}%",
            ctx_style(band.ctx_avg, pal),
            cells,
            pal,
            tail="avg",
        )
    )
    t.append_text(_rows_of(host, per))
    t.append("\n")

    if band.gauges:
        per_q = min(2, len(band.gauges)) if narrow else len(band.gauges)
        qcells = (width - 2 * (per_q - 1)) // per_q
        quota: list[Text] = []
        for g in band.gauges:
            style = severity_style(g.severity, pal)
            value = f"{g.percent:.0f}%"
            if g.severity == "critical":
                value = blink(value, frame)
            quota.append(
                _gauge(
                    g.label,
                    g.percent,
                    value,
                    style,
                    qcells,
                    pal,
                    value_style=style,
                    tail=_reset(g.resets_in_s),
                )
            )
        t.append_text(_rows_of(quota, per_q))
        t.append("\n")

    live = [c for c in snapshot.cards if c.ghost_since is None]
    working = sum(1 for c in live if c.state == "working")
    idle = [c for c in live if c.state != "working"]

    def count(cat: str) -> int:
        if cat == "error":
            return sum(1 for c in idle if c.attention == "error" or c.state == "error")
        if cat == "done":
            return sum(
                1 for c in idle if c.state != "error" and c.attention in ("", "done")
            )
        return sum(1 for c in idle if c.attention == cat and c.state != "error")

    segments = [
        Text(band.host, style=f"bold {pal.accent}"),
        Text(
            f"✻ {working} working",
            style=pulse(pal.working, frame, pal) if working else pal.muted,
        ),
    ]
    for cat, noun in _COUNTERS:
        n = count(cat)
        if n and cat == "needs_input":
            seg = Text.from_markup(mark(cat, pal, blink_off=frame % 2 == 1))
        elif n and cat == "error":
            # Blinks here but not in the tab bar, so not through ``mark``.
            seg = Text.from_markup(
                f"[bold {pal.panel} on {pal.error}] {blink(GLYPHS[cat], frame)} [/]"
            )
        else:
            segments.append(
                Text(f"{GLYPHS[cat]} {n} {noun}", style=pal.ink if n else pal.muted)
            )
            continue
        seg.append(f" {n} {noun}", style=style_for(cat, pal))
        segments.append(seg)
    tail = [f"${band.cost_live:.2f} live"]
    if band.recap_calls:
        tail.append(f"recap ${band.recap_cost:.2f} / {band.recap_calls}")
    running, configured = band.queues
    tail += [f"queues {running}/{configured}", f"monitors {band.monitors}"]
    if band.build:
        tail.append(Text.from_markup(band.build[-1]).plain)
    if band.clock:
        tail.append(band.clock)
    segments += [Text(text, style=pal.muted) for text in tail]

    # Drop whole segments from the end until the row fits; never cut one.
    sep = 3  # " · "
    while len(segments) > 1 and (
        sum(x.cell_len for x in segments) + sep * (len(segments) - 1) > width
    ):
        segments.pop()
    row = Text(" · ", style=pal.muted).join(segments)
    row.truncate(width)
    t.append_text(row)
    return t
