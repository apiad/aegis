"""Sidebar — the F3 dashboard column.

F3 toggles a *mode*, not a widget. Open, this column holds every ambient
surface a pane has (plan, queues, monitors, and all eight status-bar
segments) and the main column is transcript and input. Closed, the pane is
what it was before this file existed.

The four collapsed surfaces each solve the same problem separately — too
much state, one row — and each solves it by throwing information away
(`fit` exists because Textual clips an over-long line silently). A terminal
is usually much taller than it is full; this spends the vertical axis
instead.

Two pieces, the shape every strip here already uses:
* ``render_sidebar(model, palette, width)`` — pure Rich Text renderer.
* ``Sidebar`` — the Textual widget.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.text import Text

from aegis.monitor.schema import MonitorView
from aegis.plan.models import PlanState
from aegis.tui.fit import Segment, fit_rows


@dataclass
class SidebarModel:
    """Everything the sidebar renders, assembled from the sources the
    strips and the status bar already read. No new data path."""

    # SESSION
    connection: tuple[str, ...] = ()
    title: str = ""
    identity: tuple[str, ...] = ()
    state_label: str = ""
    loop: tuple[str, ...] = ()
    # CONTEXT
    metrics: tuple[str, ...] = ()
    quota: tuple[str, ...] = ()
    # PLAN
    plan: PlanState | None = None
    subplans: dict = field(default_factory=dict)
    plan_working: bool = False
    plan_frame: int = 0
    # QUEUES
    queues: object | None = None          # aegis.queue.digest.Snapshot
    # MONITORS
    monitors: list[MonitorView] = field(default_factory=list)
    # SYSTEM
    system: tuple[str, ...] = ()


def heading(text: str, palette, width: int, right: str = "") -> Text:
    """A section heading, optionally with a right-aligned counter.

    Padded in cells rather than characters — the counter is ASCII, but the
    budget it is padded against is shared with rows that are not.
    """
    out = Text(text, style=f"bold {palette.muted}")
    if right:
        pad = width - cell_len(text) - cell_len(right)
        if pad >= 1:
            out.append(" " * pad)
            out.append(right, style=palette.muted)
    return out


def _rows(segments, palette, width: int) -> list[Text]:
    return [Text.from_markup(r) for r in fit_rows(segments, width)]


def _block(head: Text, rows: list[Text]) -> Text:
    out = Text()
    out.append_text(head)
    for r in rows:
        out.append("\n")
        out.append_text(r)
    return out


def _session(m: SidebarModel, palette, width: int) -> Text | None:
    # Connection leads: a disconnected session is a fact about the session,
    # and it is the one segment that demands action. Under its own heading
    # at some scroll offset it would be worse than the bar it replaces.
    segs = [
        Segment("connection", m.connection, 0),
        Segment("title", (m.title,) if m.title else (), 0),
        Segment("identity", m.identity, 0),
        Segment("state", (m.state_label,) if m.state_label else (), 0),
        Segment("loop", m.loop, 0),
    ]
    rows = _rows(segs, palette, width)
    if not rows:
        return None
    return _block(heading("SESSION", palette, width), rows)


def _context(m: SidebarModel, palette, width: int) -> Text | None:
    segs = [Segment("metrics", m.metrics, 0),
            Segment("quota", m.quota, 0)]
    rows = _rows(segs, palette, width)
    if not rows:
        return None
    return _block(heading("CONTEXT", palette, width), rows)


# Ordered by volatility, highest first — what changes and what demands
# action at the top, what never changes at the bottom. Same principle as
# StatusBar's priority ladder, turned ninety degrees: the panel scrolls, so
# what you see without scrolling should be what moves.
SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...] = (
    _session, _context,
)


def render_sidebar(model: SidebarModel, palette, width: int) -> Text:
    """The whole column. A section that renders ``None`` contributes
    nothing — not a heading, not a blank row."""
    blocks = [b for b in (s(model, palette, width) for s in SECTIONS)
              if b is not None]
    out = Text()
    for i, b in enumerate(blocks):
        if i:
            out.append("\n\n")
        out.append_text(b)
    return out
