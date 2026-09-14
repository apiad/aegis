"""A brain wired the way `cli.py::_serve` wires one, for tests that open views.

A bridged view adopts the brain's planes and refuses to open over a brain
that lacks one (see `aegis.core.planes`), so a bare `SessionManager` no
longer stands in for the daemon's. It never really did: `_serve` attaches
every plane before any view exists, and a test brain without them was
exercising a shape production never boots.

Same signature as `SessionManager`, so a call site changes only the name.
"""
from __future__ import annotations

from aegis.canvas.manager import CanvasManager
from aegis.canvas.notify import make_canvas_notifier
from aegis.core.manager import SessionManager
from aegis.monitor import MonitorManager
from aegis.queue import InboxRouter, QueueManager, ReminderService
from aegis.terminal.manager import TerminalManager
from aegis.terminal.notify import make_terminal_notifier


def make_brain(*args, queues: dict | None = None, **kw) -> SessionManager:
    kw.setdefault("inbox", InboxRouter())
    mgr = SessionManager(*args, **kw)
    inbox, roots = mgr.inbox_router, mgr.roots
    mgr.attach_queue_manager(QueueManager(queues or {}, mgr, inbox))
    mgr.attach_monitor_manager(MonitorManager(inbox, mgr))
    mgr.attach_reminder_service(ReminderService(inbox, mgr))
    mgr.attach_canvas_manager(CanvasManager(
        state_dir=roots.state_dir, notifier=make_canvas_notifier(inbox)))
    tm = TerminalManager(state_dir=roots.state_dir / "terminals",
                         default_cwd=roots.harness_cwd)
    tm.set_notifier(make_terminal_notifier(inbox))
    mgr.attach_terminal_manager(tm)
    return mgr
