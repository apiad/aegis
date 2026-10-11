"""The quota gate in the real app: the fake claude, a stub reading, a member."""

import asyncio
import json
import time

import pytest

from aegis.ops import Caller, OpError, Principal

from .conftest import until
from .test_agents import CONFIG, World

BEN = Principal("ben", "member")
LINES = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50, "stop": 90}}}}


def seed(world, session=40.0, weekly=10.0, projected=None, read_at=None):
    """Make every reading the app takes say ``session``% of Claude's 5 hours."""
    now = time.time()

    def snapshot():
        return {
            "providers": [
                {
                    "name": "claude",
                    "label": "Claude",
                    "harness": "claude-code",
                    "state": "ok",
                    "read_at": now - 30 if read_at is None else read_at,
                    "windows": [
                        {
                            "kind": "session",
                            "label": "5 hours",
                            "percent": session,
                            "projected": projected,
                            "resets_at": now + 3600,
                        },
                        {
                            "kind": "weekly_all",
                            "label": "week",
                            "percent": weekly,
                            "projected": None,
                            "resets_at": now + 86400,
                        },
                    ],
                }
            ]
        }

    world.app.quota.snapshot = snapshot
    world.app.quota.check()


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    await w.app.registry.call(
        "token.policy", {"name": "ben", "policy": LINES}, Caller("user")
    )
    yield w
    await w.stop()


def as_(principal):
    return Caller("user", principal=principal)


async def test_the_gate_follows_the_reading(world):
    app = world.app
    for pct, state in [(40, "open"), (63, "closed"), (91, "stopped"), (40, "open")]:
        seed(world, session=pct)
        assert app.gate("ben", "claude-code", "opus").state == state, pct
        assert app.gate(app.user, "claude-code", "opus").state == "open"
        assert app._gates[("ben", "claude")] == state


async def spawn_as(world, principal):
    seed(world, session=40)
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus"}, as_(principal)
    )
    return world.session(r["log_id"])


async def test_a_closed_gate_refuses_a_members_spawn_with_the_why(world):
    seed(world, session=63)
    with pytest.raises(OpError) as e:
        await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    assert e.value.code == "quota_closed"
    m = e.value.message
    assert "ben cannot start" in m and "63%" in m and "50%" in m and "resets at" in m
    s = await world.spawn()  # the admin's
    assert s.running


async def test_a_closed_gate_keeps_a_members_tasks_pending_without_spinning(world):
    seed(world, session=63)
    q, cwd = world.app.queues, str(world.root)
    bens = [
        await asyncio.wait_for(
            q.enqueue("solo", "hello", False, None, cwd, owner="ben"), 3
        )
        for _ in range(2)
    ]
    admins = await asyncio.wait_for(q.enqueue("solo", "hello", False, None, cwd), 3)
    await until(lambda: admins.status == "completed", what="the admin's task")
    assert [t.status for t in bens] == ["pending", "pending"]
    assert "63%" in q.status(bens[0])["waiting_for_quota"]
    seed(world, session=40)
    await until(
        lambda: all(t.status == "completed" for t in bens),
        timeout=15,
        what="ben's tasks",
    )


async def test_a_message_to_a_stopped_session_waits_for_the_gate(world):
    s = await spawn_as(world, BEN)
    await s.stop()
    seed(world, session=63)
    await s.deliver("> from agent:ana · now", "please look")
    assert not s.running and [m["body"] for m in s.held] == ["please look"]
    assert s.held[0]["held_for"] == "quota"
    card = s.wire()
    assert card["attention"] == "waiting"
    assert any("quota" in w for w in card["waiting_on"].split(" · ")), card[
        "waiting_on"
    ]
    seed(world, session=40)
    await until(lambda: not s.held and s.running, what="the held message sent")
    await until(lambda: s.status == "idle", what="its turn")
    assert "please look" in json.dumps(s.entries())


async def test_a_person_cannot_restart_a_gated_session_by_typing(world):
    s = await spawn_as(world, BEN)
    await s.stop()
    seed(world, session=63)
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.send", {"log_id": s.log_id, "text": "go on"}, as_(BEN)
        )
    assert e.value.code == "quota_closed" and not s.running
