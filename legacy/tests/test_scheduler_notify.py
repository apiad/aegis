"""Tests for the maybe_notify policy gate."""
from __future__ import annotations

from unittest.mock import MagicMock

from aegis.scheduler.notify import Notifier, maybe_notify


def test_notify_on_failure_default_true() -> None:
    notifier = Notifier(MagicMock())
    maybe_notify(notifier, {}, schedule="eod", status="failed:crash")
    notifier._send.assert_called_once()
    msg = notifier._send.call_args[0][0]
    assert "eod" in msg and "failed:crash" in msg


def test_notify_skipped_on_failure_when_disabled() -> None:
    notifier = Notifier(MagicMock())
    entry = {"notify": {"on_failure": False}}
    maybe_notify(notifier, entry, schedule="eod", status="failed:crash")
    notifier._send.assert_not_called()


def test_no_notify_on_success_when_disabled() -> None:
    notifier = Notifier(MagicMock())
    entry = {"notify": {"on_failure": True, "on_success": False}}
    maybe_notify(notifier, entry, schedule="eod", status="ok")
    notifier._send.assert_not_called()


def test_notify_on_success_when_enabled() -> None:
    notifier = Notifier(MagicMock())
    entry = {"notify": {"on_success": True}}
    maybe_notify(notifier, entry, schedule="eod", status="ok")
    notifier._send.assert_called_once()
    msg = notifier._send.call_args[0][0]
    assert "eod" in msg and "ok" in msg


def test_none_notifier_is_safe() -> None:
    maybe_notify(None, {}, schedule="x", status="ok")
