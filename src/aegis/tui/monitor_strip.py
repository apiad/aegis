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


def render_monitors(views: list[MonitorView], palette) -> Text:
    if not views:
        return Text("")
    line = Text()
    line.append("monitors: ", style=palette.muted)
    for i, v in enumerate(views):
        if i:
            line.append("  ·  ", style=palette.muted)
        line.append_text(_format_mon(v, palette))
    return line


class MonitorStrip(Static):
    """One-row strip; hidden (display:none) when no monitor is live."""

    DEFAULT_CSS = """
    MonitorStrip { height: 1; padding: 0 2; margin-bottom: 1;
                   background: $panel; color: $foreground; }
    MonitorStrip.-empty { display: none; }
    """

    def __init__(self, manager, palette) -> None:
        super().__init__("", id="monitor-strip")
        self._manager = manager
        self._palette = palette
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
        views = self._manager.snapshot()
        if not views:
            self.add_class("-empty")
            self.update("")
            return
        self.remove_class("-empty")
        self.update(render_monitors(views, self._palette))
