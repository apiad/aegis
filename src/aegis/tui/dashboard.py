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
    DEFAULT_CLASSES = "_Band"

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


def _format_task_row(t, palette, mode: str,
                     *, selected: bool = False) -> Text:
    """One-line task row. mode is 'inflight' | 'queued' | 'recent'."""
    pal = palette
    line = Text()
    cursor = "▶" if selected else " "
    line.append(f"{cursor} ", style=pal.accent if selected else pal.muted)
    if mode == "inflight":
        line.append("● ", style=pal.work)
        line.append(t.worker_handle or "—", style=pal.ink)
    elif mode == "queued":
        line.append("○ —          ", style=pal.muted)
    else:  # recent
        glyph, style = (("✓", pal.ok) if t.state == "ok"
                        else ("✗", pal.err))
        line.append(f"{glyph} ", style=style)
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
        selected = getattr(self.screen, "selected_task_id", None)
        t = Text()
        t.append("IN-FLIGHT\n", style=f"bold {self._palette.accent}")
        if not running:
            t.append("  (none)\n", style=self._palette.muted)
        for row in running:
            t.append_text(_format_task_row(
                row, self._palette, "inflight",
                selected=(row.task_id == selected)))
        self._inner.update(t)


class QueuedBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        queued = [x for x in snap.tasks if x.state == "queued"]
        selected = getattr(self.screen, "selected_task_id", None)
        t = Text()
        t.append("QUEUED\n", style=f"bold {self._palette.accent}")
        if not queued:
            t.append("  (none)\n", style=self._palette.muted)
        for row in queued:
            t.append_text(_format_task_row(
                row, self._palette, "queued",
                selected=(row.task_id == selected)))
        self._inner.update(t)


class RecentBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        recent = [x for x in snap.tasks if x.state in ("ok", "err")]
        selected = getattr(self.screen, "selected_task_id", None)
        t = Text()
        t.append("RECENT\n", style=f"bold {self._palette.accent}")
        if not recent:
            t.append("  (none)\n", style=self._palette.muted)
        for row in recent:
            t.append_text(_format_task_row(
                row, self._palette, "recent",
                selected=(row.task_id == selected)))
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
        Binding("up",    "cursor_prev",    "Up",      priority=True),
        Binding("down",  "cursor_next",    "Down",    priority=True),
        Binding("enter", "refresh_detail", "Refresh", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected_task_id: str | None = None
        self._unsub = None

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

    def on_mount(self) -> None:
        self._unsub = self.app.queue_digest._manager.subscribe(
            self._on_event)
        self._ensure_selection()
        self._refresh_bands()

    def on_unmount(self) -> None:
        unsub = getattr(self, "_unsub", None)
        if unsub is not None:
            unsub()
            self._unsub = None

    def _on_event(self, _ev) -> None:
        self._ensure_selection()
        self._refresh_bands()

    def _ordered_task_ids(self) -> list[str]:
        snap = self.app.queue_digest.snapshot()
        order = []
        order += [t.task_id for t in snap.tasks if t.state == "running"]
        order += [t.task_id for t in snap.tasks if t.state == "queued"]
        order += [t.task_id for t in snap.tasks if t.state in ("ok", "err")]
        return order

    def _ensure_selection(self) -> None:
        ids = self._ordered_task_ids()
        if not ids:
            self.selected_task_id = None
            return
        if self.selected_task_id not in ids:
            self.selected_task_id = ids[0]

    def _refresh_bands(self) -> None:
        for w in self.query("._Band"):
            w.refresh_render()

    def action_cursor_next(self) -> None:
        self._ensure_selection()
        ids = self._ordered_task_ids()
        if not ids:
            return
        i = ids.index(self.selected_task_id)
        self.selected_task_id = ids[(i + 1) % len(ids)]
        self._refresh_bands()

    def action_cursor_prev(self) -> None:
        self._ensure_selection()
        ids = self._ordered_task_ids()
        if not ids:
            return
        i = ids.index(self.selected_task_id)
        self.selected_task_id = ids[(i - 1) % len(ids)]
        self._refresh_bands()

    def action_refresh_detail(self) -> None:
        self._refresh_bands()

    def action_dismiss(self) -> None:
        self.dismiss()
