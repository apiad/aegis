"""Owners end to end: the real app, the fake claude, calls made as a principal."""

import json

import pytest

from aegis.ops import Caller, OpError, Principal

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn

BEN = Principal("ben", "member")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    # A member with no policy starts nothing (quota_gate.py): open the pool.
    w.app.principals.set_default_policy({"claude": {"mode": "open"}})
    yield w
    await w.stop()


def as_(principal):
    return Caller("user", principal=principal)


async def test_a_person_owns_what_they_spawn(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    assert world.session(r["log_id"]).meta()["owner"] == "ben"


async def test_a_members_agent_cannot_launder_ownership(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    a = world.session(r["log_id"])
    out = await turn(a, mcp("session_spawn", agent="opus"))
    child = json.loads(out.removeprefix("mcp ok: "))["log_id"]
    assert world.session(child).meta()["owner"] == "ben"


@pytest.mark.quarantine("#776")
async def test_a_queue_worker_is_owned_by_whoever_enqueued(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    a = world.session(r["log_id"])
    out = await turn(a, mcp("queue_enqueue", queue="general", payload="hello"))
    task_id = json.loads(out.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(lambda: t.worker, what="the worker")
    assert t.owner == "ben" and world.session(t.worker).meta()["owner"] == "ben"


async def test_a_session_from_before_owners_is_the_admins(world):
    s = await world.spawn()  # the server's own call: the admin
    await world.stop()  # a shutdown rewrites the meta, so edit it after
    meta = world.root / ".aegis" / "state" / "sessions" / f"{s.log_id}.json"
    m = json.loads(meta.read_text())
    m.pop("owner", None)
    meta.write_text(json.dumps(m))
    await world.start()
    assert world.app.owner_of(s.log_id) == world.app.user


async def test_a_member_reads_an_admins_session_and_cannot_change_it(world):
    s = await world.spawn()
    ben = as_(BEN)
    reg = world.app.registry
    assert await reg.call("transcript.search", {"log_id": s.log_id, "q": "x"}, ben) == {
        "ids": []
    }
    for op, params in [
        ("session.send", {"log_id": s.log_id, "text": "hi"}),
        ("session.stop", {"log_id": s.log_id}),
        ("session.interrupt", {"log_id": s.log_id}),
        ("session.configure", {"log_id": s.log_id, "model": "sonnet"}),
        ("session.close", {"log_id": s.log_id}),
        ("recap.request", {"log_id": s.log_id}),
    ]:
        with pytest.raises(OpError) as e:
            await reg.call(op, params, ben)
        assert e.value.code == "not_owner", op


async def test_a_member_drives_its_own_session(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    await world.app.registry.call(
        "session.send", {"log_id": r["log_id"], "text": "hello"}, as_(BEN)
    )
    await until(lambda: world.session(r["log_id"]).status == "idle", what="the turn")


async def test_server_settings_are_for_admins(world):
    reg = world.app.registry
    snap = await reg.call("config.read", {}, as_(BEN))
    for op, params in [
        ("config.write", {"doc": snap["doc"], "stamp": snap["stamp"]}),
        ("link.add", {"url": "http://127.0.0.1:9", "token": "x"}),
        ("link.remove", {"name": "far"}),
    ]:
        with pytest.raises(OpError) as e:
            await reg.call(op, params, as_(BEN))
        assert e.value.code == "not_for_members", op


async def test_a_member_cannot_spawn_across_a_link(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, as_(BEN))
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.send",
            {"log_id": r["log_id"], "text": "/spawn opus@far hi"},
            as_(BEN),
        )
    assert e.value.code == "not_for_members"


async def test_an_admin_manages_tokens_and_a_member_cannot(world):
    reg = world.app.registry
    r = await reg.call("token.add", {"name": "cara", "role": "member"})
    assert world.app.principals.resolve(r["token"]) == Principal("cara", "member")
    names = [t["name"] for t in (await reg.call("token.list", {}))["tokens"]]
    assert names[0] == world.app.user and "cara" in names
    assert (await reg.call("token.revoke", {"name": "cara"}))["token"] is None
    with pytest.raises(OpError) as e:
        await reg.call("token.list", {}, as_(BEN))
    assert e.value.code == "not_for_members"


async def test_a_member_cannot_prompt_or_interrupt_anothers_session(world):
    s = await world.spawn()  # the admin's
    reg = world.app.registry
    with pytest.raises(OpError) as e:
        await reg.call(
            "peer.handoff",
            {"target": s.handle, "context": "do this", "interrupt": True},
            as_(BEN),
        )
    assert e.value.code == "not_owner"
    r = await reg.call("session.spawn", {"agent": "opus"}, as_(BEN))
    a = world.session(r["log_id"])
    out = await turn(a, mcp("peer_handoff", target=s.handle, context="do this"))
    assert "not_owner" in out
    mine = (await reg.call("session.spawn", {"agent": "opus"}, as_(BEN)))["log_id"]
    landed = await reg.call(
        "peer.handoff",
        {"target": world.session(mine).handle, "context": "hi"},
        as_(BEN),
    )
    assert world.session(mine).handle in landed


async def test_a_member_cannot_cancel_a_monitor_on_anothers_session(world):
    s = await world.spawn()  # the admin's
    out = await turn(
        s,
        mcp(
            "monitor_start",
            description="wait",
            done="sleep 60",
            progress=None,
            source=None,
        ),
    )
    monitor_id = json.loads(out.removeprefix("mcp ok: "))["monitor_id"]
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "monitor.cancel", {"monitor_id": monitor_id}, as_(BEN)
        )
    assert e.value.code == "not_owner"
    assert monitor_id in world.app.monitors.items


POLICY = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50, "stop": 90}}}}


async def test_an_admin_sets_a_policy_and_the_default(world):
    reg = world.app.registry
    await reg.call("token.policy", {"name": "ben", "policy": POLICY}, Caller("user"))
    await reg.call(
        "token.policy",
        {"name": "*", "policy": {"claude": {"mode": "open"}}},
        Caller("user"),
    )
    listed = await reg.call("token.list", {}, Caller("user"))
    assert listed["default_policy"] == {"claude": {"mode": "open"}}
    assert {r["name"]: r["policy"] for r in listed["tokens"]}["ben"] == POLICY
    assert world.app.principals.policy_of("ben") == POLICY
    await reg.call("token.policy", {"name": "ben", "policy": None}, Caller("user"))
    assert world.app.principals.policy_of("ben") == {"claude": {"mode": "open"}}


async def test_a_member_cannot_set_a_policy_and_a_bad_one_is_refused(world):
    reg = world.app.registry
    with pytest.raises(OpError) as e:
        await reg.call("token.policy", {"name": "ben", "policy": None}, as_(BEN))
    assert e.value.code == "not_for_members"
    with pytest.raises(OpError) as e:
        await reg.call(
            "token.policy", {"name": "ben", "policy": {"nope": {}}}, Caller("user")
        )
    assert e.value.code == "bad_policy"


async def test_the_token_list_carries_the_pools_and_each_members_gates(world):
    reg = world.app.registry
    await reg.call("token.policy", {"name": "ben", "policy": POLICY}, Caller("user"))
    listed = await reg.call("token.list", {}, Caller("user"))
    pools = {p["name"]: p for p in listed["pools"]}
    assert set(pools) == {"claude", "opencode-go", "codex"}
    assert [w["kind"] for w in pools["claude"]["windows"]] == ["session", "weekly_all"]
    assert pools["codex"]["windows"] == [] and pools["claude"]["windows"][1]["pace"]
    rows = {r["name"]: r for r in listed["tokens"]}
    # No reading in a test: ben's lines close claude; the default opens it.
    assert rows["ben"]["gates"]["claude"] == "closed"
    assert rows["ben"]["gates"]["codex"] == "closed"
    assert listed["default_gates"]["claude"] == "open"
    assert rows[world.app.user]["gates"] is None
