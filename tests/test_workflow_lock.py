import types

import pytest

from aegis.ops import OpError

from .test_agents import World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    from .test_agents import CONFIG

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_a_node_session_refuses_a_person_and_a_peer_while_its_run_is_live(world):
    node = await world.spawn()
    node.workflow = {"run": "r1", "node": "write"}
    real = world.app.workflows  # None before task 7, the engine after
    world.app.workflows = types.SimpleNamespace(live=lambda run: run == "r1")
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
    world.app.workflows = real
