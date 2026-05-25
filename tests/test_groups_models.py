from __future__ import annotations

from aegis.groups.models import (
    BroadcastRecord,
    Group,
    GroupResult,
    MemberRef,
    MemberResult,
)


def test_group_holds_named_members():
    g = Group(name="reviewers", members={
        "ada-knuth":   MemberRef(handle="ada-knuth",   profile="security"),
        "lucid-hopper": MemberRef(handle="lucid-hopper", profile="style"),
    })
    assert g.name == "reviewers"
    assert set(g.members) == {"ada-knuth", "lucid-hopper"}
    assert g.members["ada-knuth"].profile == "security"


def test_group_result_aggregates_member_results():
    res = GroupResult(
        broadcast_id="br-1",
        by_member={
            "a": MemberResult(handle="a", text="x", turn_ms=10,
                              tokens_in=1, tokens_out=1, status="done"),
        },
        combined="x",
        errors={},
        timeouts=[],
    )
    assert res.broadcast_id == "br-1"
    assert res.by_member["a"].status == "done"
    assert res.combined == "x"


def test_broadcast_record_carries_four_field_contract():
    rec = BroadcastRecord(
        id="br-1", group="reviewers", sender="agent:host",
        objective="audit X", output_format="markdown",
        tool_guidance="read-only", boundaries="20 file reads max",
        started_at="2026-05-25T08:00:00Z", members=("a", "b", "c"),
    )
    assert rec.id == "br-1"
    assert rec.objective == "audit X"
    assert rec.members == ("a", "b", "c")
