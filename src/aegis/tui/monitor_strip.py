"""MonitorStrip — always-on, one-line process-monitor summary.

Sits above the status bar (mirrors QueueStrip). Two pieces:
* ``render_monitors(views, palette)`` — pure Rich Text renderer.
* ``MonitorStrip`` — Textual Static widget subscribed to a MonitorManager,
  re-rendering on each change. Hidden when no monitors are live.
"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from aegis.monitor.schema import MonitorView


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _bar(pct: float, width: int = 8) -> str:
    fill = int(round(pct / 100.0 * width))
    fill = max(0, min(width, fill))
    return "▓" * fill + "░" * (width - fill)


def _format_mon(v: MonitorView, palette) -> Text:
    t = Text()
    t.append(v.description, style=palette.ink)
    if v.pct is not None:
        t.append(f"  {_bar(v.pct)} ", style=palette.work)
        t.append(f"{v.pct:.0f}%", style=palette.ink)
        if v.eta_s is not None:
            t.append(f" · ETA {_fmt_dur(v.eta_s)}", style=palette.muted)
    else:
        t.append(f"  ⣾ {_fmt_dur(v.elapsed_s)} watching", style=palette.muted)
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
        out.append_text(_format_mon(v, palette))
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
