from __future__ import annotations

import json
from pathlib import Path

from aegis.groups.persistence import (
    PersistedGroupLog,
    event_broadcast_started,
    event_created,
    event_member_added,
)


def test_writes_events_one_per_line(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_broadcast_started(
        "br-1", "o", "f", "t", "b", "agent:host", ("ada",)))

    p = tmp_path / "groups" / "rev.jsonl"
    lines = p.read_text().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["kind"] == "created"
    assert parsed[1]["kind"] == "member_added"
    assert parsed[2]["kind"] == "broadcast_started"
    assert parsed[2]["broadcast_id"] == "br-1"


def test_registry_writes_events_via_log(tmp_path: Path):
    from aegis.groups.models import MemberRef
    from aegis.groups.registry import GroupRegistry

    log = PersistedGroupLog(tmp_path)
    reg = GroupRegistry(log=log)
    reg.add_member("rev", MemberRef("ada", "sec"), sender="agent:host")
    reg.remove_member("rev", "ada", reason="closed-by-user")
    lines = log.read("rev")
    kinds = [r["kind"] for r in lines]
    assert kinds == ["created", "member_added", "member_removed",
                     "dissolved"]
