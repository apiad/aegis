"""A callback a widget hands to the brain runs as the widget's own app (#35).

The brain is one object shared by every view in the daemon, and it calls its
observers from its own tasks: an MCP tool handler, the queue, a harness's
stdout reader. Those tasks carry no Textual ``active_app``, or another view's.
Whatever a widget starts from such a callback copies that context: a timer, a
mounted child's message pump, a worker. A timer's first tick reads
``active_app`` and dies with ``LookupError``, and nothing notices until the
widget is removed and ``Timer._stop_all`` awaits the dead task, which
re-raises and takes the whole view down. That is the crash on Ctrl+W, on
leaving F10 and on quit.

So every callback a widget subscribes on the brain goes through
``as_own_app``. Patching the timers one at a time (the roster snapshot, the
queue spawn's mount, the workflow scheduler) left every other one exposed.
"""

from __future__ import annotations

from typing import Any, Callable

from textual._context import active_app
from textual.app import App
from textual.dom import DOMNode


def _own_app(node: DOMNode | None) -> App | None:
    """The app ``node`` is mounted in. Not ``node.app``: that reads
    ``active_app`` first, which is exactly what is wrong here."""
    while node is not None and not isinstance(node, App):
        node = node._parent
    return node


class as_own_app:
    """``fn``, called with ``widget``'s app as ``active_app``.

    Equal to ``fn``, so ``remove_event_observer(self._on_event)`` still
    finds the wrapper that ``add_event_observer`` stored: the brain's lists
    remove by equality. ``__self__`` is the widget, as on a bound method, so
    ``View._owns`` still finds a detached view's leftovers.
    """

    __slots__ = ("_widget", "_fn")

    def __init__(self, widget: DOMNode, fn: Callable[..., Any]) -> None:
        self._widget = widget
        self._fn = fn

    @property
    def __self__(self) -> DOMNode:
        return self._widget

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        app = _own_app(self._widget)
        if app is None:  # not mounted yet, or already gone
            return self._fn(*args, **kwargs)
        token = active_app.set(app)
        try:
            return self._fn(*args, **kwargs)
        finally:
            active_app.reset(token)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, as_own_app):
            other = other._fn
        return self._fn == other

    def __hash__(self) -> int:
        return hash(self._fn)
