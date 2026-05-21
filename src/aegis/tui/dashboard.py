"""QueueDashboard — modal observability surface for the queue substrate."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static

from aegis.queue.digest import QueueDigest


class _Band(Widget):
    DEFAULT_CSS = """
    _Band { height: auto; padding: 0; margin-bottom: 1; }
    """

    def __init__(self, digest: QueueDigest, palette,
                 *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._digest = digest
        self._palette = palette
        self._unsub = None
        self._inner = Static("")

    def compose(self) -> ComposeResult:
        yield self._inner

    def on_mount(self) -> None:
        self._unsub = self._digest._manager.subscribe(
            lambda ev: self.refresh_render())
        self.refresh_render()

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def refresh_render(self) -> None:
        raise NotImplementedError


class QueuesBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        pal = self._palette
        t = Text()
        t.append("QUEUES\n", style=f"bold {pal.accent}")
        for q in snap.queues:
            t.append(f"\n  {q.name}\n", style=pal.ink)
            t.append("    agent ", style=pal.muted)
            t.append(q.agent, style=pal.accent)
            t.append(" · parallel ", style=pal.muted)
            t.append(f"{q.max_parallel}\n", style=pal.ink)
            t.append("    running ", style=pal.muted)
            t.append(f"{q.running}", style=pal.work)
            t.append(" · queued ", style=pal.muted)
            t.append(f"{q.queued}", style=pal.work)
            if q.ok:
                t.append(" · ", style=pal.muted)
                t.append(f"✓{q.ok}", style=pal.ok)
            if q.err:
                t.append(" ", style=pal.muted)
                t.append(f"✗{q.err}", style=pal.err)
        self._inner.update(t)


def _format_task_row(t, palette, mode: str) -> Text:
    """One-line task row. mode is 'inflight' | 'queued' | 'recent'."""
    pal = palette
    line = Text()
    if mode == "inflight":
        line.append(" ● ", style=pal.work)
        line.append(t.worker_handle or "—", style=pal.ink)
    elif mode == "queued":
        line.append(" ○ —          ", style=pal.muted)
    else:  # recent
        glyph, style = (("✓", pal.ok) if t.state == "ok"
                        else ("✗", pal.err))
        line.append(f" {glyph} ", style=style)
        line.append(
            (t.worker_handle or "—").ljust(14)[:14], style=pal.muted)
    line.append(f"  {t.queue:<8}", style=pal.muted)
    line.append(f"  {t.payload_summary}", style=pal.ink)
    line.append("\n")
    return line


class InFlightBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        running = [x for x in snap.tasks if x.state == "running"]
        t = Text()
        t.append("IN-FLIGHT\n", style=f"bold {self._palette.accent}")
        if not running:
            t.append("  (none)\n", style=self._palette.muted)
        for row in running:
            t.append_text(_format_task_row(row, self._palette, "inflight"))
        self._inner.update(t)


class QueuedBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        queued = [x for x in snap.tasks if x.state == "queued"]
        t = Text()
        t.append("QUEUED\n", style=f"bold {self._palette.accent}")
        if not queued:
            t.append("  (none)\n", style=self._palette.muted)
        for row in queued:
            t.append_text(_format_task_row(row, self._palette, "queued"))
        self._inner.update(t)


class RecentBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        recent = [x for x in snap.tasks if x.state in ("ok", "err")]
        t = Text()
        t.append("RECENT\n", style=f"bold {self._palette.accent}")
        if not recent:
            t.append("  (none)\n", style=self._palette.muted)
        for row in recent:
            t.append_text(_format_task_row(row, self._palette, "recent"))
        self._inner.update(t)


class QueueDashboard(ModalScreen):
    CSS = """
    QueueDashboard { align: center middle; background: $background; }
    QueueDashboard #wrap { width: 100%; height: 100%;
                           background: $background; padding: 1 2; }
    QueueDashboard #left  { width: 2fr; height: 1fr; }
    QueueDashboard #right { width: 1fr; height: 1fr;
                            border-left: solid $foreground 20%;
                            padding-left: 2; }
    QueueDashboard #footer { dock: bottom; height: 1;
                             color: $foreground 60%; padding: 0 2; }
    """
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        digest = self.app.queue_digest
        palette = self.app.palette
        with Vertical(id="wrap"):
            with Horizontal():
                with Vertical(id="left"):
                    yield QueuesBand(digest, palette, id="band-queues")
                    yield InFlightBand(digest, palette, id="band-inflight")
                    yield QueuedBand(digest, palette, id="band-queued")
                    yield RecentBand(digest, palette, id="band-recent")
                with Vertical(id="right"):
                    yield Static("DETAIL", id="detail")
            yield Static(
                "↑↓ select  enter focus  > jump to tab  esc collapse",
                id="footer")

    def action_dismiss(self) -> None:
        self.dismiss()
