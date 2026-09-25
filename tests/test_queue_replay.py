"""Every lifecycle event maps to a status, and every status has a branch.

Membership in `_LIFECYCLE_EVENTS` used to mean `status = event name`. A
`resumed` record under that rule sets a status of "resumed", which matches
no branch, which drops the task out of `_all` with no callback and blocks
its producer forever. That is a fix for one hang introducing another, and
this file exists so the next person cannot do it by adding one line.
"""
from __future__ import annotations

from aegis.queue.manager import _LIFECYCLE_EVENTS
from aegis.queue.replay import EVENT_STATUS, REPLAY_BRANCHES


def test_every_lifecycle_event_has_a_status():
    missing = _LIFECYCLE_EVENTS - set(EVENT_STATUS)
    assert not missing, (
        f"lifecycle events with no status mapping: {sorted(missing)}. "
        "Without one, replay sets the status to the event name and the "
        "task matches no branch."
    )


def test_every_mapped_status_has_a_replay_branch():
    missing = set(EVENT_STATUS.values()) - REPLAY_BRANCHES
    assert not missing, (
        f"statuses with no replay branch: {sorted(missing)}. A task in one "
        "of these vanishes on restart and its producer waits forever."
    )


def test_resumed_maps_back_to_dispatched():
    assert EVENT_STATUS["resumed"] == "dispatched"
