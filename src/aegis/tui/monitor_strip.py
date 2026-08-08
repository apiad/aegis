"""MonitorStrip — always-on, one-line process-monitor summary.

Sits above the status bar (mirrors QueueStrip). Two pieces:
* ``render_monitors(views, palette)`` — pure Rich Text renderer.
* ``MonitorStrip`` — Textual Static widget subscribed to a MonitorManager,
  re-rendering on each change. Hidden when no monitors are live.
"""
from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from aegis.monitor.schema import MonitorView
from aegis.tui.fit import truncate_cells


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _bar(pct: float, width: int = 8) -> str:
    fill = int(round(pct / 100.0 * width))
    fill = max(0, min(width, fill))
    return "▓" * fill + "░" * (width - fill)


# A description narrower than this says nothing — "the full h…" does not
# identify which of three monitors it is. The tail gives up its optional
# parts before the description is cut below it.
_DESC_FLOOR = 14


def _tail_tiers(v: MonitorView, palette) -> list[Text]:
    """The row's fixed half, widest first. Same ladder idea as the status
    bar's segments: drop detail rather than let the row overflow."""
    def t(*parts: tuple[str, str]) -> Text:
        out = Text()
        for text, style in parts:
            out.append(text, style=style)
        return out

    if v.pct is None:
        # The spinner already says "watching", so the word is the first
        # thing to go.
        dur = _fmt_dur(v.elapsed_s)
        return [t((f"  ⣾ {dur} watching", palette.muted)),
                t((f"  ⣾ {dur}", palette.muted))]

    pct = (f"{v.pct:.0f}%", palette.ink)
    bar = (f"  {_bar(v.pct)} ", palette.work)
    if v.eta_s is None:
        return [t(bar, pct), t(("  ", palette.muted), pct)]
    eta = (f" · ETA {_fmt_dur(v.eta_s)}", palette.muted)
    return [t(bar, pct, eta), t(("  ", palette.muted), pct, eta),
            t(("  ", palette.muted), pct)]


def format_mon(v: MonitorView, palette, width: int | None = None) -> Text:
    """One monitor's description and bar. Shared with the sidebar's
    MONITORS section, so the bar is drawn the same way in both.

    ``width`` bounds the whole row. Unbounded by default, which is the
    strip: the row is the full pane there, and the bar sits far right of
    any real description. The sidebar is 26 cells and its body is a Static
    inside a VerticalScroll, so an over-long row *wraps* rather than
    clips — one monitor becomes three rows and pushes SYSTEM off the
    panel.

    Both halves give way, in order: the tail drops its optional detail
    until the description clears `_DESC_FLOOR`, and only then is the
    description itself cut. Cutting the row from the right instead would
    take the bar, the percentage and the ETA — everything it exists to
    show — while keeping a description already legible at half the width.
    """
    tiers = _tail_tiers(v, palette)
    if width is None:
        tail, budget = tiers[0], None
    else:
        floor = min(cell_len(v.description), _DESC_FLOOR)
        tail = next((x for x in tiers if width - x.cell_len >= floor),
                    tiers[-1])
        budget = max(1, width - tail.cell_len)

    desc = v.description if budget is None else truncate_cells(
        v.description, budget)
    t = Text(desc, style=palette.ink)
    t.append_text(tail)
    return t


_LABEL = "monitors: "


def render_monitors(views: list[MonitorView], palette) -> Text:
    """One monitor per row — they stack rather than sharing a line, so a
    long description never pushes another monitor's bar off the edge."""
    if not views:
        return Text("")
    out = Text()
    for i, v in enumerate(views):
        if i:
            out.append("\n")
        out.append(_LABEL if i == 0 else " " * len(_LABEL),
                   style=palette.muted)
        out.append_text(format_mon(v, palette))
    return out


class MonitorStrip(Static):
    """One row per live monitor; hidden (display:none) when none are."""

    DEFAULT_CSS = """
    MonitorStrip { height: auto; padding: 0 2; margin-bottom: 1;
                   background: $panel; color: $foreground; }
    MonitorStrip.-empty { display: none; }
    """

    def __init__(self, manager, palette, handle_of=None) -> None:
        """``handle_of`` is a callable returning the handle to scope to —
        read on every refresh, because a session can rename itself and the
        monitors it arms afterwards carry the new name."""
        super().__init__("", id="monitor-strip")
        self._manager = manager
        self._palette = palette
        self._handle_of = handle_of
        self._unsub = None

    def set_palette(self, palette) -> None:
        self._palette = palette
        self._refresh()

    def on_mount(self) -> None:
        self._unsub = self._manager.subscribe(self._refresh)
        self._refresh()

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _refresh(self) -> None:
        handle = self._handle_of() if self._handle_of is not None else None
        views = self._manager.snapshot(for_handle=handle)
        if not views:
            self.add_class("-empty")
            self.update("")
            return
        self.remove_class("-empty")
        self.update(render_monitors(views, self._palette))
