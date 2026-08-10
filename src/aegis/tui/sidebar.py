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
from textual.containers import VerticalScroll
from textual.widgets import Static

from aegis.monitor.schema import MonitorView
from aegis.plan.models import PlanState
from aegis.plan.render import render_plan_dock
from aegis.repos.models import RepoView
from aegis.repos.render import render_repos
from aegis.tui.fit import Segment, fit_rows
from aegis.tui.monitor_strip import format_mon
from aegis.tui.strip import format_q


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
    # REPOS
    repos: list[RepoView] = field(default_factory=list)
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


def _plan(m: SidebarModel, palette, width: int) -> Text | None:
    if not m.plan and not m.subplans:
        return None
    # PlanState already computes both — do not re-derive them.
    done = m.plan.done if m.plan else 0
    total = m.plan.total if m.plan else 0
    # render_plan_dock verbatim: it already space-separates the circles
    # (East Asian Ambiguous — Rich measures one cell, terminals draw two,
    # neighbours overlap) and budgets labels at width - 9. Re-implementing
    # rows here would re-pay both bugs.
    body = render_plan_dock(m.plan or PlanState(), palette,
                            working=m.plan_working, frame=m.plan_frame,
                            width=width, subplans=m.subplans)
    head = heading("PLAN", palette, width,
                   right=f"{done}/{total}" if total else "")
    # The dock was a free-standing surface, so it opens with its own
    # `tasks d/t` line and ends every row — including the last — with a
    # newline. As a *section* it needs neither: `head` already carries the
    # counter at the right edge, and the trailing newline lands between
    # the "\n\n" separators below as a second blank row, which reads as a
    # missing section rather than as spacing. Trimmed at the composition
    # site rather than in `render_plan_dock`: that function is a pure
    # renderer with its own contract in `tests/test_plan_render.py` (the
    # header and the `(no plan)` body are both asserted there), and this
    # is a fact about framing it in a section, not about how a row looks.
    return _block(head, list(body.split("\n", allow_blank=False))[1:])


def _queues(m: SidebarModel, palette, width: int) -> Text | None:
    snap = m.queues
    if snap is None or not snap.queues:
        return None
    return _block(heading("QUEUES", palette, width),
                  [format_q(q, palette, width) for q in snap.queues])


def _monitors(m: SidebarModel, palette, width: int) -> Text | None:
    if not m.monitors:
        return None
    # One monitor per row rather than sharing a line — the strip's rule,
    # for the same reason: a long description must not push another
    # monitor's bar off the edge.
    return _block(heading("MONITORS", palette, width),
                  [format_mon(v, palette, width) for v in m.monitors])


def _repos(m: SidebarModel, palette, width: int) -> Text | None:
    body = render_repos(m.repos, palette, width)
    if body is None:
        return None
    # The counter is the section's own, not the renderer's: `render_repos`
    # draws rows and nothing else, the way `format_q` and `format_mon` do,
    # and the heading with its right-aligned count is what every section
    # here shares.
    head = heading("REPOS", palette, width, right=str(len(m.repos)))
    return _block(head, list(body.split("\n", allow_blank=False)))


def _system(m: SidebarModel, palette, width: int) -> Text | None:
    rows = _rows([Segment("system", m.system, 0)], palette, width)
    if not rows:
        return None
    return _block(heading("SYSTEM", palette, width), rows)


# Ordered by volatility, highest first — what changes and what demands
# action at the top, what never changes at the bottom. Same principle as
# StatusBar's priority ladder, turned ninety degrees: the panel scrolls, so
# what you see without scrolling should be what moves.
SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...] = (
    _session, _context, _plan, _queues, _monitors, _repos, _system,
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


_TICK = 0.25
# Proportional, not a magic number, and inherited from the dock this
# replaces: across 344 real task subjects the median is 35 characters and
# p90 is 49, so no fixed width works. A share of the pane adapts instead,
# and the bounds keep it useful on an 80-col terminal without eating a
# 200-col one.
# Named because `_width`'s no-layout fallback has to subtract exactly what
# the CSS adds. The two drift apart the moment the padding changes, and the
# only symptom is a never-shown tab truncating to the wrong budget.
SIDEBAR_PAD_Y = 1
SIDEBAR_PAD_X = 2
SIDEBAR_PCT = 34
# The bounds are the *frame*, so they carry the padding on top of the
# content budget the dock was tuned against (24..56). Padding is chrome and
# the rows must not pay for it: charging them the 4 columns took an 80-col
# terminal below the widest system segment, which `fit_rows` then dropped
# outright — a section vanishing, not a row getting shorter.
SIDEBAR_MIN = 26 + 2 * SIDEBAR_PAD_X
SIDEBAR_MAX = 60 + 2 * SIDEBAR_PAD_X


class Sidebar(VerticalScroll):
    """The F3 column. Scrolls: fully populated it is ~25 rows, and an 80x24
    terminal has about twenty to give."""

    # `$panel` is the token the four strips this absorbs already sat on.
    # Sharing it is what makes the column read as one surface rather than
    # text floating beside a transcript, which is on `$background`.
    DEFAULT_CSS = f"""
    Sidebar {{
        width: {SIDEBAR_PCT}%;
        min-width: {SIDEBAR_MIN};
        max-width: {SIDEBAR_MAX};
        padding: {SIDEBAR_PAD_Y} {SIDEBAR_PAD_X};
        background: $panel;
        color: $foreground;
        scrollbar-size: 0 0;
    }}
    """

    def __init__(self, palette, **kw) -> None:
        super().__init__(**kw)
        self._palette = palette
        self._model = SidebarModel()
        self._body = Static("")
        self._open = False
        self._paints = 0        # test seam for the closed-mode no-op
        self.display = False

    def compose(self):
        yield self._body

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def set_palette(self, palette) -> None:
        self._palette = palette
        if self._open:
            self._paint()

    def on_resize(self) -> None:
        """Repaint at the real width.

        A widget that was display:none has not been laid out, so size.width
        is still 0 and the first paint after a toggle falls back to the
        minimum — truncating far harder than the real width requires.
        """
        if self._open:
            self._paint()

    def _tick(self) -> None:
        # Repaint only while a task is actually running: a settled panel is
        # static and must not burn a redraw four times a second.
        if self._open and self._model.plan_working:
            self._model.plan_frame += 1
            self._paint()

    def set_open(self, opened: bool) -> bool:
        self._open = opened
        self.display = opened
        if opened:
            self._paint()
        return self._open

    def toggle(self) -> bool:
        return self.set_open(not self._open)

    @property
    def is_open(self) -> bool:
        return self._open

    def refresh_model(self, model: SidebarModel) -> None:
        """Replace the snapshot; repaint only when open.

        The model is stored either way — it is a dataclass, and dropping it
        while closed would make the first frame after a toggle stale. What
        the closed mode skips is the *render*, which is the expensive half.
        Same discipline PlanDock.refresh_plan used.
        """
        model.plan_frame = self._model.plan_frame
        self._model = model
        if self._open:
            self._paint()

    def _width(self) -> int:
        # `size` is already the content box — Textual excludes padding from
        # it — so the padding above must NOT be subtracted again. The
        # fallback is the other case: before the first layout `size` is 0,
        # and there the frame has to be turned into a content box by hand.
        return self.size.width or SIDEBAR_MIN - 2 * SIDEBAR_PAD_X

    def _paint(self) -> None:
        self._paints += 1
        self._body.update(
            render_sidebar(self._model, self._palette, self._width()))

    def plain(self) -> str:
        """The current column as plain text. A test seam: reaching into a
        Static's renderable couples the tests to Textual's internals."""
        return render_sidebar(
            self._model, self._palette, self._width()).plain
