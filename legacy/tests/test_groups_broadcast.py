from __future__ import annotations

import pytest

from aegis.groups.broadcast import BroadcastInFlight, BroadcastTracker
from aegis.groups.models import BroadcastRecord


def _rec(rid: str, group: str, members: tuple[str, ...]) -> BroadcastRecord:
    return BroadcastRecord(
        id=rid, group=group, sender="agent:host",
        objective="o", output_format="of", tool_guidance="tg", boundaries="b",
        started_at="2026-05-25T08:00:00Z", members=members,
    )


def test_open_then_get():
    bt = BroadcastTracker()
    rec = _rec("br-1", "reviewers", ("a", "b"))
    bt.open(rec)
    assert bt.current("reviewers") is rec


def test_second_open_on_same_group_raises():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "reviewers", ("a",)))
    with pytest.raises(BroadcastInFlight) as ei:
        bt.open(_rec("br-2", "reviewers", ("a",)))
    assert "br-1" in str(ei.value)


def test_close_frees_the_slot():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "reviewers", ("a",)))
    bt.close("reviewers", "br-1")
    bt.open(_rec("br-2", "reviewers", ("a",)))


def test_distinct_groups_independent():
    bt = BroadcastTracker()
    bt.open(_rec("br-1", "a", ("x",)))
    bt.open(_rec("br-2", "b", ("y",)))
