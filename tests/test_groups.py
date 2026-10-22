"""Groups (#586): a session belongs to the group of the session that started
it, fixed at spawn on SpawnSpec.group, so the bar and the Fleet can gather a
spawner and everything it spawned or enqueued."""

import json

from aegis.ops import Caller

from .conftest import until
from .test_agents import mcp, turn, world  # noqa: F401


async def child_of(world, parent):  # noqa: F811
    said = await turn(parent, mcp("session_spawn", agent="opus"))
    return world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])


async def test_a_spawn_chain_shares_the_roots_group(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    c = await child_of(world, b)
    assert a.spec.group is None
    assert b.spec.group == c.spec.group == a.log_id
    assert b.wire()["group"] == a.log_id


async def test_the_group_survives_the_root_closing_and_a_reload(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    c = await child_of(world, b)
    assert c.spec.group == a.log_id
    assert world.app.sessions._session(c.meta()).spec.group == a.log_id


async def test_a_person_spawn_typed_in_a_session_joins_its_group(world):  # noqa: F811
    a = await world.spawn()
    r = await world.app.registry.call(
        "session.send", {"log_id": a.log_id, "text": "/spawn opus"}, Caller("user")
    )
    b = world.session(r["log_id"])
    assert b.spec.group == a.log_id
    # A person started it: its questions are the person's, not a's (notices.py).
    assert b.spec.spawned_by is None


async def test_a_person_spawn_from_a_session_through_the_form_joins_its_group(world):  # noqa: F811
    a = await world.spawn()
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus", "from_session": a.log_id}, Caller("user")
    )
    assert world.session(r["log_id"]).spec.group == a.log_id


async def test_a_worker_joins_its_enqueuers_group(world):  # noqa: F811
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/busy 1"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: t.worker, timeout=8, what="the worker")
    assert world.session(t.worker).spec.group == a.log_id


async def test_an_agent_cannot_move_its_child_into_another_group(world):  # noqa: F811
    a = await world.spawn()
    other = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", from_session=other.log_id))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    assert child.spec.group == a.log_id


async def test_an_old_meta_has_no_group(world):  # noqa: F811
    a = await world.spawn()
    old = a.meta()
    old.pop("group", None)
    assert world.app.sessions._session(old).spec.group is None
