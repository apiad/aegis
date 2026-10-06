from __future__ import annotations

from pathlib import Path

from aegis.groups.persistence import (
    PersistedGroupLog,
    event_broadcast_started,
    event_created,
    event_member_added,
)
from aegis.groups.registry import GroupRegistry


def test_replay_reconstitutes_members(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_member_added("lucid", "logic", "agent:host", "T"))

    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada", "lucid"})
    assert set(reg.get("rev").members) == {"ada", "lucid"}


def test_replay_marks_lost_when_session_is_gone(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_member_added("lost", "logic", "agent:host", "T"))

    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada"})
    assert "lost" not in reg.get("rev").members
    kinds = [r["kind"] for r in log.read("rev")]
    assert kinds[-1] == "member_removed"


def test_replay_marks_orphan_broadcast_failed_interrupted(tmp_path: Path):
    log = PersistedGroupLog(tmp_path)
    log.write("rev", event_created("rev", "agent:host", "T"))
    log.write("rev", event_member_added("ada", "sec", "agent:host", "T"))
    log.write("rev", event_broadcast_started(
        "br-1", "o", "f", "t", "b", "agent:host", ("ada",)))
    reg = GroupRegistry(log=log)
    reg.start(live_handles={"ada"})
    kinds = [r["kind"] for r in log.read("rev")]
    assert "broadcast_completed" in kinds
    last = [r for r in log.read("rev")
            if r["kind"] == "broadcast_completed"][-1]
    assert last["mode"] == "failed:interrupted"
