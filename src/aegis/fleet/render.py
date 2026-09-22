"""The fleet dashboard, drawn as Rich Text.

Pure, in the shape of ``aegis.tui.sidebar.render_sidebar``: a model in, a
``Text`` out, no Textual object, no clock and no manager. Motion is a
``frame`` number passed in, so frame 0 and frame 1 are both testable.
"""

from __future__ import annotations

import time

from rich.cells import cell_len
from rich.text import Text

from aegis.fleet.models import CardView, EventLine, FleetSnapshot, Origin

_BAR = "█"
_EMPTY = "░"
_EVENTS = 3  # the activity tail's depth
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


def _body(text: str) -> str:
    """``text`` for a multi-line body: newlines stay, a tab becomes a space,
    and every other C0 or C1 control character is dropped. A carriage return
    or an escape sequence would move the cursor inside the widget."""
    return "".join(
        " "
        if ch == "\t"
        else ""
        if (ch < " " and ch != "\n") or "\x7f" <= ch <= "\x9f"
        else ch
        for ch in text
    )


def _state_style(state: str, pal) -> str:
    return {"working": pal.working, "error": pal.error}.get(state, pal.ready)


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
    # The label already names the action ("read render.py", "Run the
    # suite"), so the tool name would only repeat it; it is the fallback for
    # a call that produced no label at all.
    return f"{time.strftime('%H:%M', time.localtime(ev.at))} {ev.summary or ev.tool}"


def gauge(
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


def rows_of(gauges: list[Text], per_row: int) -> Text:
    t = Text()
    for i in range(0, len(gauges), per_row):
        if i:
            t.append("\n")
        for j, g in enumerate(gauges[i : i + per_row]):
            if j:
                t.append("  ")
            t.append_text(g)
    return t


def reset_in(seconds: float | None) -> str:
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
            gauge("CPU", s.cpu, f"{s.cpu:.0f}%", ctx_style(s.cpu, pal), cells, pal),
            gauge(
                "RAM",
                s.ram,
                f"{s.ram:.0f}%",
                ctx_style(s.ram, pal),
                cells,
                pal,
                tail=ram_tail,
            ),
            gauge("DSK", s.disk, f"{s.disk:.0f}%", ctx_style(s.disk, pal), cells, pal),
        ]
    host.append(
        gauge(
            "CTX",
            band.ctx_avg,
            f"{band.ctx_avg:.0f}%",
            ctx_style(band.ctx_avg, pal),
            cells,
            pal,
            tail="avg",
        )
    )
    t.append_text(rows_of(host, per))
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
                gauge(
                    g.label,
                    g.percent,
                    value,
                    style,
                    qcells,
                    pal,
                    value_style=style,
                    tail=reset_in(g.resets_in_s),
                )
            )
        t.append_text(rows_of(quota, per_q))
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


def _lead(card: CardView, pal, frame: int) -> Text:
    """The item's first cell: the pulsing working dot, the pending attention
    mark (``needs_input`` and ``error`` blink here), or the resting dot."""
    from aegis.attention import GLYPHS, mark

    if card.state == "working":
        return Text("●", style=pulse(pal.working, frame, pal))
    if card.attention == "error":
        return Text.from_markup(
            f"[bold {pal.panel} on {pal.error}] {blink(GLYPHS['error'], frame)} [/]"
        )
    if card.attention:
        return Text.from_markup(mark(card.attention, pal, blink_off=frame % 2 == 1))
    return Text("●", style=pal.error if card.state == "error" else pal.ready)


def _where(card: CardView) -> str:
    return _one_line(
        _origin_line(card.origin) if card.origin.ephemeral else _identity(card)
    )


def render_item(card: CardView, pal, frame: int) -> Text:
    """One list item, at least three lines. Line 1 and the where line are one
    line each; ``did`` and ``now`` keep every word and line, and the widget
    wraps them."""
    from aegis.attention import LABELS, style_for

    t = _lead(card, pal, frame)
    num = "⏱" if card.origin.ephemeral else str(card.tab_index or "")
    t.append(f" {num} ", style=pal.muted)
    t.append(_one_line(card.handle), style=f"bold {pal.ink}")
    t.append("  ")
    t.append_text(bar(card.ctx_pct, 6, ctx_style(card.ctx_pct, pal), pal))
    t.append(f" {card.ctx_pct:.0f}%", style=pal.muted)
    if card.monitors:
        t.append(f" ◉{len(card.monitors)}", style=pal.accent)
    if card.ghost_since is not None:
        t.append(f"  closed {_age(card.ghost_s)} ago", style=pal.muted)
    elif card.state == "working":
        t.append(f"  working {_age(card.turn_s)}", style=pal.working)
    elif card.attention:
        t.append(f"  {LABELS[card.attention]}", style=style_for(card.attention, pal))
    else:
        t.append(f"  up {_age(card.uptime_s)}", style=pal.muted)
    t.append("\n")
    t.append(_where(card), style=pal.accent if card.origin.ephemeral else pal.muted)
    t.append("\n")
    if card.doing and (card.state == "working" or not card.did):
        t.append("now ", style=pal.muted)
        t.append(_body(card.doing), style=pal.working)
    elif card.did:
        t.append("did ", style=pal.muted)
        t.append(_body(card.did), style=pal.ink)
    else:
        for i, ev in enumerate(card.events[-_EVENTS:]):
            if i:
                t.append("\n")
            t.append(_one_line(_event(ev)), style=pal.muted)
    return t


def _tokens(n: int) -> str:
    """``920k``, ``1M``, ``1.5M``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".removesuffix(".0") + "M"
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


def _detail_header(card: CardView, pal, frame: int) -> Text:
    from aegis.attention import LABELS, style_for

    t = Text(_one_line(card.handle), style=f"bold {pal.accent}")
    t.append("  ")
    if card.state != "working" and card.attention:
        t.append_text(_lead(card, pal, frame))
        t.append(f" {LABELS[card.attention]}", style=style_for(card.attention, pal))
        t.append("  ")
    t.append(
        f"● {card.state}",
        style=pulse(_state_style(card.state, pal), frame, pal)
        if card.state == "working"
        else _state_style(card.state, pal),
    )
    if card.state == "working":
        t.append(f"  turn {_age(card.turn_s)}", style=pal.working)
    if card.title:
        t.append("\n")
        t.append(_one_line(card.title), style=f"bold {pal.ink}")
    facts = [_where(card), f"up {_age(card.uptime_s)}"]
    if card.tab_index and not card.origin.ephemeral:
        facts.append(f"tab {card.tab_index}")
    t.append("\n")
    t.append(" · ".join(f for f in facts if f), style=pal.muted)
    return t


def _monitors(card: CardView, pal, width: int, frame: int) -> Text:
    cells = max(10, width - 60)
    t = Text()
    for i, m in enumerate(card.monitors):
        if i:
            t.append("\n")
        t.append("◉ ", style=pulse(pal.accent, frame, pal))
        t.append(_one_line(m.description), style=pal.ink)
        t.append(" ")
        if m.pct is None:
            t.append_text(sweep_bar(cells, frame, pal))
        else:
            t.append_text(bar(m.pct, cells, pal.accent, pal))
            t.append(f" {m.pct:.0f}%", style=pal.ink)
        t.append(f" {_age(m.elapsed_s)}", style=pal.muted)
        eta = "no ETA" if m.eta_s is None else f"ETA {_age(m.eta_s)}"
        t.append(f" {eta}", style=pal.muted)
    return t


def _gauges(card: CardView, pal, width: int) -> Text:
    cells = max(10, width - 30)
    rows: list[Text] = []

    def row(label: str, pct: float, style: str, value: str, tail: str = "") -> None:
        r = Text(f"{label:<5}", style=pal.muted)
        r.append_text(bar(pct, cells, style, pal))
        r.append(f" {value}", style=pal.ink)
        if tail:
            r.append(f" {tail}", style=pal.muted)
        rows.append(r)

    row(
        "ctx",
        card.ctx_pct,
        ctx_style(card.ctx_pct, pal),
        f"{card.ctx_pct:.0f}%",
        f"{_tokens(card.ctx_tokens)}/{_tokens(card.ctx_window)}"
        if card.ctx_window
        else "",
    )
    if card.plan_total:
        row(
            "plan",
            100 * card.plan_done / card.plan_total,
            pal.ready,
            f"{card.plan_done}/{card.plan_total}",
        )
    span = max(card.avg_turn_s, card.turn_s)
    if card.state == "working" and span > 0:
        row(
            "turn",
            100 * card.turn_s / span,
            pal.working,
            _age(card.turn_s),
            f"avg {_age(card.avg_turn_s)}" if card.avg_turn_s else "",
        )
    return Text("\n").join(rows)


_PLAN_GLYPH = {"completed": "●", "in_progress": "◐"}  # anything else is pending


def _plan(card: CardView, pal) -> Text:
    t = Text()
    for i, task in enumerate(card.plan_tasks):
        if i:
            t.append("\n")
        glyph = _PLAN_GLYPH.get(task.status, "○")
        style = {"●": pal.ready, "◐": pal.working}.get(glyph, pal.muted)
        t.append(f"{glyph} ", style=style)
        t.append(_one_line(task.subject), style=pal.ink)
    return t


def render_detail(card: CardView, pal, width: int, frame: int) -> Text:
    """The selected session in full. ``width`` sizes the bars only; the pane
    wraps text. A section with nothing to say is left out, heading and all."""
    spend = [f"${card.cost_usd:.2f}"]
    if card.claims:
        spend.append(f"{card.claims} claims")
    if card.spoke_with:
        spend.append(f"← {card.spoke_with[0]}")
    if card.waiting_on:
        spend.append(f"→ {card.waiting_on[0]}")
    sections = [
        ("TASK", Text(_body(card.task), style=pal.ink)),
        ("NOW", Text(_body(card.doing), style=pal.working)),
        ("DID", Text(_body(card.did), style=pal.ink)),
        ("NEXT", Text(_body(card.next), style=pal.ink)),
        (f"MONITORS · {len(card.monitors)}", _monitors(card, pal, width, frame)),
        ("GAUGES", _gauges(card, pal, width)),
        ("PLAN", _plan(card, pal)),
        (
            "ACTIVITY",
            Text("\n", style=pal.muted).join(
                Text(_one_line(_event(ev)), style=pal.muted)
                for ev in card.events[-_EVENTS:]
            ),
        ),
        ("SPEND · COORDINATION", Text(" · ".join(spend), style=pal.ink)),
    ]
    t = _detail_header(card, pal, frame)
    for heading, body in sections:
        if not body.plain:
            continue
        t.append("\n\n")
        t.append(heading, style=pal.muted)
        t.append("\n")
        t.append_text(body)
    return t
