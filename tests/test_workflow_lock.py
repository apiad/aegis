"""A node's session is the workflow's while its run is live: every way in from
a person, a peer, a schedule, a page or the fleet's nudges is refused at the
session, and what the node itself started (its monitors) still reaches it."""

import asyncio

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn
from .test_artifacts_e2e import arts, landed
from .test_schedules_e2e import create, frozen, runs, tick


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def lock(world, node) -> None:
    """Make ``node`` the node `write` of a live run r1."""
    node.workflow = {"run": "r1", "node": "write"}
    world.app.workflows.live = lambda run: run == "r1"


async def test_a_node_session_refuses_a_person_and_a_peer_while_its_run_is_live(world):
    node = await world.spawn()
    lock(world, node)
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.send", {"log_id": node.log_id, "text": "hi"}
        )
    assert e.value.code == "in_workflow" and "r1" in e.value.message
    with pytest.raises(OpError):
        await world.app.registry.call("session.interrupt", {"log_id": node.log_id})
    other = await world.spawn()
    said = await turn(other, mcp("peer_handoff", target=node.handle, context="psst"))
    assert "workflow r1" in said
    world.app.workflows.live = lambda run: False
    await world.app.registry.call(
        "session.send", {"log_id": node.log_id, "text": "now ok"}
    )


async def test_the_session_itself_refuses_and_only_the_engine_passes(world):
    node = await world.spawn()
    lock(world, node)
    for call in (
        node.deliver("> from x · now", "steer"),
        node.send("steer"),
        node.interrupt(),
    ):
        with pytest.raises(OpError) as e:
            await call
        assert e.value.code == "in_workflow"
    await node.deliver("> from workflow:w · nudge · now", "go on", through_lock=True)
    await until(lambda: inbox(node), timeout=5, what="the engine's message")


async def test_a_handoff_from_a_linked_server_is_refused(world):
    node = await world.spawn()
    lock(world, node)
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "peer.deliver",
            {
                "target": node.handle,
                "context": "steer",
                "sender": {"handle": "far-agent", "server": "far", "user": "u"},
            },
            Caller("user", link="far"),
        )
    assert e.value.code == "in_workflow"
    assert not inbox(node)


async def test_a_schedule_into_a_node_session_records_the_refusal(world):
    t = frozen(world)
    node = await world.spawn()
    lock(world, node)
    create(
        world, "poke-node", "steer", fire={"target": "existing", "session": node.log_id}
    )
    await tick(world, t)
    (r,) = runs(world, "poke-node", "tick")
    assert r["out"] == "error" and "workflow r1" in r["err"], r
    assert not inbox(node)


async def test_a_handoff_of_a_node_session_is_refused(world):
    node = await world.spawn()
    lock(world, node)
    with pytest.raises(OpError) as e:
        await world.app.registry.call("session.handoff", {"log_id": node.log_id})
    assert e.value.code == "in_workflow"
    assert node.continued_by is None


async def test_a_page_the_node_sent_cannot_steer_it(world, tmp_path):
    node, aid = await landed(world, tmp_path)
    lock(world, node)
    reg = world.app.registry
    ref = {"log_id": node.log_id, "artifact_id": aid}
    for op, extra in (
        ("artifact.submit", {"data": {"p": "b"}, "label": "B"}),
        ("artifact.emit", {"name": "pick", "data": {"p": "b"}}),
        ("artifact.error", {"message": "boom", "stack": ""}),
    ):
        with pytest.raises(OpError) as e:
            await reg.call(op, ref | extra)
        assert e.value.code == "in_workflow", op
    assert not inbox(node)
    assert arts(node)[0]["status"] != "submitted"


async def test_the_fleet_does_not_nudge_a_node(world, monkeypatch):
    monkeypatch.setattr(world.app.nudges, "timings", lambda: (0.2, 900.0))
    node, control = await world.spawn(), await world.spawn()
    await turn(node, "write the summary")
    await turn(control, "write the summary")
    lock(world, node)
    await asyncio.sleep(0.5)
    await world.app.nudges.tick()
    await until(lambda: inbox(control), timeout=5, what="the control's nudge")
    assert not inbox(node)


@pytest.mark.slow
async def test_a_nodes_own_monitor_still_wakes_it(world, tmp_path):
    node = await world.spawn()
    flag = tmp_path / "flag"
    said = await turn(
        node,
        mcp(
            "monitor_start",
            description="w",
            done=f"test -f {flag}",
            progress=None,
            source=None,
        ),
    )
    assert said.startswith("mcp ok"), said
    lock(world, node)
    flag.write_text("")
    await until(
        lambda: any("> from monitor:" in (e.get("md") or "") for e in inbox(node)),
        timeout=15,
        what="the monitor's wake",
    )


async def test_a_node_session_cannot_be_stopped_closed_configured_or_renamed(world):
    node = await world.spawn()
    lock(world, node)
    reg = world.app.registry
    for op, extra in (
        ("session.stop", {}),
        ("session.restart", {}),
        ("session.close", {}),
        ("session.configure", {"effort": "low"}),
        ("session.rename", {"title": "Mine now"}),
    ):
        with pytest.raises(OpError) as e:
            await reg.call(op, {"log_id": node.log_id} | extra)
        assert e.value.code == "in_workflow" and "r1" in e.value.message, op
    assert node.log_id in world.app.sessions.sessions and not node.archived
    assert node.title != "Mine now"
    world.app.workflows.live = lambda run: False
    await reg.call("session.rename", {"log_id": node.log_id, "title": "Mine now"})


async def test_an_agent_cannot_close_or_rename_a_node_session(world):
    main = await world.spawn()
    node = await world.spawn()
    lock(world, node)
    said = await turn(main, mcp("session_close", handle=node.handle))
    assert "workflow r1" in said and node.log_id in world.app.sessions.sessions
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.rename", {"title": "Renamed"}, Caller("agent", node.log_id)
        )
    assert e.value.code == "in_workflow" and node.title != "Renamed"
