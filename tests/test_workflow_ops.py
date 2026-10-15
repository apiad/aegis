import pytest

from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import RESULT, flow, ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_lint_names_every_error_and_arms_nothing(world):
    a = await world.spawn()
    said = await turn(
        a,
        mcp(
            "workflow_lint",
            definition="id: x\ntitle: X\nsay: s\nroot: {seq: [{agent: {agent: nobody, prompt: p}}]}",
        ),
    )
    out = ok(said)
    assert (
        "$.root.seq[0]: every node needs say: one sentence for the person"
        in out["errors"]
    )
    assert "$.root.seq[0].agent: no agent nobody in .aegis.yaml" in out["errors"]
    assert world.app.workflows.runs == {}


async def test_an_agent_cannot_approve_reject_or_arm_twice(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    assert (await turn(a, mcp("workflow_approve", run=run))).startswith("mcp error")
    assert (await turn(a, mcp("workflow_reject", run=run))).startswith("mcp error")
    assert world.app.workflows.runs[run].status == "armed"
    assert "already has workflow run" in await turn(
        a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={})
    )


async def test_a_person_rejects_and_the_session_can_arm_again(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    await world.app.registry.call("workflow.reject", {"run": run})
    assert world.app.workflows.runs[run].status == "rejected"
    ok(await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={})))


async def test_status_returns_the_tree(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    st = ok(await turn(a, mcp("workflow_status", run=run)))
    assert [r["id"] for r in st["tree"]] == ["n1", "one", "two"] or [
        r["say"] for r in st["tree"]
    ][1:] == ["Do the first thing.", "Do the second thing."]
