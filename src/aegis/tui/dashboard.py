"""QueueDashboard — modal observability surface for the queue substrate."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


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
        with Vertical(id="wrap"):
            with Horizontal():
                with Vertical(id="left"):
                    yield Static("QUEUES", id="band-queues")
                    yield Static("IN-FLIGHT", id="band-inflight")
                    yield Static("QUEUED", id="band-queued")
                    yield Static("RECENT", id="band-recent")
                with Vertical(id="right"):
                    yield Static("DETAIL", id="detail")
            yield Static(
                "↑↓ select  enter focus  > jump to tab  esc collapse",
                id="footer")

    def action_dismiss(self) -> None:
        self.dismiss()
