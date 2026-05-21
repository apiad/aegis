"""QueueStrip — always-on, one-line queue summary above the status bar.

Two pieces:
* ``render_strip(snapshot, palette)`` — pure Rich Text renderer.
* ``QueueStrip`` — Textual Static widget that subscribes to a digest
  and re-renders on each event.
"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from aegis.queue.digest import QueueDigest, QueueView, Snapshot


def _format_q(q: QueueView, palette) -> Text:
    t = Text()
    t.append(q.name, style=palette.ink)
    t.append(f" ●{q.running}", style=palette.work)
    t.append(f"/{q.max_parallel}", style=palette.muted)
    if q.queued:
        t.append(f" ○{q.queued}", style=palette.muted)
    if q.ok:
        t.append(f" ✓{q.ok}", style=palette.ok)
    if q.err:
        t.append(f" ✗{q.err}", style=palette.err)
    return t


def render_strip(snap: Snapshot, palette) -> Text:
    if not snap.queues:
        return Text("")
    line = Text()
    line.append("queues: ", style=palette.muted)
    n = len(snap.queues)
    if n <= 3:
        for i, q in enumerate(snap.queues):
            if i:
                line.append(" · ", style=palette.muted)
            line.append_text(_format_q(q, palette))
    else:
        total_running = sum(q.running for q in snap.queues)
        total_cap = sum(q.max_parallel for q in snap.queues)
        total_queued = sum(q.queued for q in snap.queues)
        total_ok = sum(q.ok for q in snap.queues)
        total_err = sum(q.err for q in snap.queues)
        line.append(f"{n} queues · ", style=palette.ink)
        line.append(f"●{total_running}/{total_cap}", style=palette.work)
        if total_queued:
            line.append(f" ○{total_queued}", style=palette.muted)
        if total_ok:
            line.append(f" ✓{total_ok}", style=palette.ok)
        if total_err:
            line.append(f" ✗{total_err}", style=palette.err)

    last = snap.last_started
    # Only show "last:" if there's still a running worker — once it
    # finishes we drop the cell rather than implying staleness.
    last_running = (last is not None and last.state == "running"
                    and last.worker_handle)
    if last_running:
        line.append("    last: ", style=palette.muted)
        line.append(last.worker_handle, style=palette.work)
    return line


class QueueStrip(Static):
    """One-row strip widget. Hidden (height: 0) when there are no
    queues; one row otherwise.
    """
    DEFAULT_CSS = """
    QueueStrip { height: 1; padding: 0 2; background: $panel;
                 color: $foreground; }
    QueueStrip.-empty { display: none; }
    """

    def __init__(self, digest: QueueDigest, palette) -> None:
        super().__init__("", id="queue-strip")
        self._digest = digest
        self._palette = palette
        self._unsub = None

    def set_palette(self, palette) -> None:
        self._palette = palette
        self._refresh()

    def on_mount(self) -> None:
        self._unsub = self._digest._manager.subscribe(
            lambda ev: self._refresh())
        self._refresh()

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _refresh(self) -> None:
        snap = self._digest.snapshot()
        if not snap.queues:
            self.add_class("-empty")
            self.update("")
            return
        self.remove_class("-empty")
        self.update(render_strip(snap, self._palette))
