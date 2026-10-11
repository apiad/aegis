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
