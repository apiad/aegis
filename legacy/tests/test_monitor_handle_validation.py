"""Regression: a monitor must refuse a handle it could never wake.

``start_monitor`` already refuses a *condition* that can never trip —
"checked here, not only at the MCP surface, so no caller can route around
it". The handle is the same class of defect one field over: a monitor's
entire purpose is the wake it delivers, and ``_fire`` delivers to
``mon.from_handle``. Point that at a handle no session answers to and the
monitor polls for its full timeout, trips, delivers into the void, and the
agent waits forever on a callback that was already sent nowhere.

Observed live on 2026-08-12. The operator renamed a session at 13:22:14;
the agent, whose context still held the old handle, armed monitors at
13:27:31 and 13:31:54 addressed to a handle that had not existed for five
minutes. Both armed happily and watched for minutes. Nothing said a word.

An agent cannot see a rename the operator makes — no message announces it,
and its system prompt still carries the handle it was born with. So a stale
``from_handle`` is not an exotic mistake; it is the expected consequence of
a rename, and the substrate is the only layer positioned to catch it.
"""
from __future__ import annotations

import pytest

from aegis.monitor.manager import MonitorManager
from aegis.queue.inbox import InboxRouter


class _Info:
    def __init__(self, handle: str) -> None:
        self.handle = handle
        self.state = "ready"
        self.unsolicited = False


class _SM:
    def __init__(self, *handles: str) -> None:
        self._sessions = [_Info(h) for h in handles]

    def list_sessions(self):
        return self._sessions


def _mm(sm=None) -> MonitorManager:
    return MonitorManager(InboxRouter(), sm)


def _arm(mm: MonitorManager, handle: str) -> str:
    return mm.start_monitor(
        from_handle=handle, description="d", done="true", autorun=False)


def test_unknown_handle_is_refused():
    mm = _mm(_SM("alive-one", "alive-two"))
    with pytest.raises(ValueError) as e:
        _arm(mm, "ghost-handle")
    msg = str(e.value)
    assert "ghost-handle" in msg
    # The message has to be actionable: name what IS live, so a renamed
    # agent can find itself rather than guess.
    assert "alive-one" in msg and "alive-two" in msg


def test_live_handle_is_accepted():
    mm = _mm(_SM("alive-one"))
    assert _arm(mm, "alive-one")


def test_no_session_manager_does_not_block():
    """Most callers in the suite build a MonitorManager with no session
    manager at all. It cannot answer the question, so it must not veto."""
    assert _arm(_mm(), "whoever")


def test_empty_session_list_does_not_block():
    """A manager reporting zero sessions is a stub or a boot-time race, not
    a fact about liveness — in production the arming session is itself live.
    Refusing every monitor in that state would be worse than the bug."""
    assert _arm(_mm(_SM()), "whoever")


def test_refusal_leaves_no_monitor_behind():
    """A rejected arm must not leave a half-registered monitor or a hold on
    a session — the roster is what the agent is told to prune against."""
    mm = _mm(_SM("alive-one"))
    with pytest.raises(ValueError):
        _arm(mm, "ghost-handle")
    assert mm.list_monitors() == []
