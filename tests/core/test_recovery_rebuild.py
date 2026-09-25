"""Rebuild swaps the subprocess under a live session. It never spawns.

Spawning onto a closed worker's handle races the async pane drop (the TUI
runs it through `run_worker`), so `#pane-<handle>` may still be mounted
and a second mount is DuplicateIds, which takes the whole app down.
`AgentSession.adopt` is the way through, and `reconnect` already uses it
for a dropped remote link.

The rig is `tests/core/conftest.py`: a SessionManager holding one local
session whose stub harness reports a conversation id, or deliberately
does not.
"""
from __future__ import annotations

from aegis.core.recovery import (
    NUDGE_OPERATOR,
    NUDGE_RESTART,
    NUDGE_STALL,
    rebuild,
)

NUDGE = "Your aegis session was interrupted mid-task. Continue."


async def test_rebuild_reconnects_in_place_and_nudges(brain_with_local_session):
    mgr, handle = brain_with_local_session
    before = len(mgr.list_sessions())

    assert await rebuild(mgr, handle, nudge=NUDGE) is True

    assert len(mgr.list_sessions()) == before, "rebuild must not add a session"
    assert mgr.get(handle) is not None, "the handle must still be live"
    assert NUDGE in mgr.get(handle).delivered_bodies[-1]


async def test_rebuild_keeps_the_observers_the_queue_attached(
    brain_with_local_session,
):
    """The whole reason a rebuild goes through adopt. If observers did not
    survive, the worker would run its recovered turn unobserved and the
    queue would never hear it end."""
    mgr, handle = brain_with_local_session
    s = mgr.get(handle)
    seen: list = []
    s.add_state_observer(lambda _s, st, finished: seen.append(st))
    s.add_event_observer(lambda _s, ev: seen.append(ev))

    assert await rebuild(mgr, handle, nudge=NUDGE) is True

    assert mgr.get(handle) is s, "adopt must keep the same AgentSession"
    assert seen, "the rebuilt session's observers still fire"


async def test_rebuild_passes_allow_local(brain_with_local_session):
    """Without it, every local worker is unrecoverable — which is most of
    them. The fixture's session is local, and plain reconnect refuses
    those (see test_reconnect_allow_local.py), so a True here can only
    come from allow_local being passed."""
    mgr, handle = brain_with_local_session
    assert mgr.get(handle).place.is_local
    assert await rebuild(mgr, handle, nudge=NUDGE) is True


async def test_rebuild_returns_false_for_an_unknown_handle(
    brain_with_local_session,
):
    mgr, _handle = brain_with_local_session
    assert await rebuild(mgr, "ghost-handle", nudge=NUDGE) is False


async def test_rebuild_returns_false_when_reconnect_raises(
    brain_with_local_session_no_id,
):
    """Review Focus 2, half one: the ACP loadSession probe can fail at
    runtime, and a raise here would take out the queue's finalizer."""
    mgr, handle = brain_with_local_session_no_id
    assert await rebuild(mgr, handle, nudge=NUDGE) is False


async def test_rebuild_does_not_nudge_when_the_rebuild_failed(
    brain_with_local_session_no_id,
):
    """A nudge delivered to a dead harness is a message nobody reads and a
    turn the task is charged for."""
    mgr, handle = brain_with_local_session_no_id
    await rebuild(mgr, handle, nudge=NUDGE)
    assert not mgr.get(handle).delivered_bodies


async def test_rebuild_returns_false_when_the_nudge_cannot_be_delivered(
    brain_with_local_session, monkeypatch,
):
    """The other half of the same argument. A harness that comes back up
    and then refuses the message has not been recovered, and reporting
    True would leave the task in flight with nobody driving it."""
    mgr, handle = brain_with_local_session

    async def boom(_msg):
        raise RuntimeError("harness went away between reconnect and deliver")

    monkeypatch.setattr(mgr.get(handle), "deliver", boom)
    assert await rebuild(mgr, handle, nudge=NUDGE) is False


async def test_the_nudge_says_the_conversation_survived():
    """A rebuilt worker has no memory of the interruption — from inside,
    the turn simply never ended. A nudge that does not say the
    conversation is intact makes it re-derive what it already knows."""
    for nudge in (NUDGE_STALL, NUDGE_RESTART, NUDGE_OPERATOR):
        assert "intact" in nudge
    assert "{reason}" in NUDGE_STALL, "the stall nudge carries the diagnostic"
    assert "monitor" in NUDGE_RESTART, (
        "a monitor lives in the process that died, so a worker resumed "
        "after a restart is waiting on a wake that will never come")
