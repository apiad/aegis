"""Sessions a node spawns count in the pool, against a real server and fake
agents: a node cannot push the server's live agents past max_agents, nor its
run's past its limits, by spawning helpers."""

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG
from .test_workflows_parallel import arm, leaf, script, world  # noqa: F401


def spawner(agent: str = "opus") -> str:
    spawn = ["session_spawn", {"agent": agent, "prompt": "/sleep 3"}]
    return script(
        spawn,
        spawn,
        ["workflow_result", {"value": {"n": 1}, "summary": "Spawned helpers."}],
    )


async def helpers(world, header: str, agent: str = "opus") -> list:
    run = await arm(
        world,
        f"""
id: helpers
title: Helpers
say: A node that spawns two helpers.
{header}
root: {leaf("n", "Spawn two.", spawner(agent))}
""",
    )
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    node = st.nodes["n"].log_id
    return [s for s in world.app.sessions.sessions.values() if s.spec.spawned_by == node]


async def test_a_node_cannot_spawn_past_the_servers_total(world):
    """max_agents total 2: the node and one helper. The second spawn is
    refused, and the node's turn goes on to its result."""
    kids = await helpers(world, "")
    assert len(kids) == 1


async def test_a_node_cannot_spawn_past_its_runs_limits(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 8}\n")
    assert len(await helpers(world, "limits: {total: 2}")) == 1


async def test_a_helper_counts_under_its_own_agents_number(world):
    (world.root / ".aegis.yaml").write_text(
        CONFIG + "workflows: {max_agents: {total: 8, reviewer: 1}}\n"
    )
    assert len(await helpers(world, "", agent="reviewer")) == 1


async def test_a_refused_spawn_says_why(world):
    wf = world.app.workflows
    run = await arm(
        world,
        f"""
id: why
title: Why
say: A node that spawns while the pool is full.
root: {leaf("n", "Spawn two.", script(["sleep", 20]))}
""",
    )
    await until(lambda: "n" in wf.runs[run].nodes, timeout=20)
    node = world.app.sessions.sessions[wf.runs[run].nodes["n"].log_id]
    me, ask = Caller("agent", log_id=node.log_id), {"agent": "opus", "prompt": "/sleep 3"}
    assert (await world.app.registry.call("session.spawn", ask, me))["log_id"]
    try:
        await world.app.registry.call("session.spawn", ask, me)
    except OpError as e:
        assert e.code == "no_room", e
        assert "max_agents" in e.message and run in e.message, e.message
    else:
        raise AssertionError("the second spawn was not refused")
