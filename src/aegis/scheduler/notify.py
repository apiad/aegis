"""Notification bridge for scheduler fire outcomes.

Each schedule entry may carry a ``notify: {on_failure, on_success}``
dict. ``maybe_notify`` consults that dict against the terminal status
of a fire and invokes ``notifier.send(msg)`` if applicable. The
notifier is supplied by the substrate (typically a Telegram-frontend
wrapper).
"""
from __future__ import annotations

from typing import Any, Callable


class Notifier:
    """Thin wrapper around a send-message callable.

    ``send_fn`` accepts a single string. The default no-op makes it
    safe to instantiate ``Scheduler`` without a Telegram frontend.
    """

    def __init__(self, send_fn: Callable[[str], Any] | None = None) -> None:
        self._send = send_fn or (lambda msg: None)

    def send(self, msg: str) -> Any:
        return self._send(msg)


def maybe_notify(notifier: Notifier | None, entry: dict, *,
                 schedule: str, status: str) -> None:
    if notifier is None:
        return
    nf = entry.get("notify") or {}
    is_failure = status != "ok"
    if is_failure and not nf.get("on_failure", True):
        return
    if not is_failure and not nf.get("on_success", False):
        return
    prefix = "⚠️" if is_failure else "✅"
    notifier.send(f"{prefix} schedule {schedule} — {status}")
