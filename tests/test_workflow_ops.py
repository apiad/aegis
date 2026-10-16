import pytest

from .conftest import until
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


async def test_an_agent_cannot_approve_reject_or_arm_beside_a_running_run(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    assert (await turn(a, mcp("workflow_approve", run=run))).startswith("mcp error")
    assert (await turn(a, mcp("workflow_reject", run=run))).startswith("mcp error")
    assert world.app.workflows.runs[run].status == "armed"
    # Armed and not approved, a new arm replaces it (test_workflows_edits.py);
    # once it runs, a second one is refused.
    await world.app.registry.call("workflow.approve", {"run": run})
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
    # The rejection wakes the session; its answer must not read as the next.
    await until(lambda: rejected(a) and a.status == "idle", timeout=10)
    ok(await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={})))


def rejected(s) -> list[str]:
    return [
        e["md"]
        for e in s.entries()
        if e["kind"] == "inbox" and "· rejected ·" in (e.get("md") or "")
    ]


async def test_a_rejected_plan_tells_the_main_agent(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    await world.app.registry.call("workflow.reject", {"run": run})
    await until(lambda: rejected(a), timeout=5)
    (md,) = rejected(a)
    assert "> from workflow:" in md and run in md and "workflow_arm" in md


async def test_status_returns_the_tree(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    st = ok(await turn(a, mcp("workflow_status", run=run)))
    assert [r["id"] for r in st["tree"]] == ["n1", "one", "two"] or [
        r["say"] for r in st["tree"]
    ][1:] == ["Do the first thing.", "Do the second thing."]


async def test_a_session_that_is_not_a_node_cannot_give_an_output(world):
    from aegis.ops import Caller, OpError

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "workflow.output",
            {"name": "timers", "value": [1], "summary": "Two timers."},
            Caller("agent", a.log_id),
        )
    assert e.value.code == "not_a_node"
    said = await turn(a, mcp("workflow_output", name="x", value=1, summary="S."))
    assert said.startswith("mcp error")


def nested(levels: int) -> str:
    node = '{sh: "true", say: Leaf.}'
    for _ in range(levels - 1):
        node = f"{{seq: [{node}], say: Group.}}"
    return f"id: d\ntitle: D\nsay: D.\nroot: {node}\n"


async def test_nodes_nest_at_most_64_levels_whatever_the_stack(world):
    a = await world.spawn()
    out = ok(await turn(a, mcp("workflow_lint", definition=nested(64))))
    assert out["errors"] == [], out
    out = ok(await turn(a, mcp("workflow_lint", definition=nested(65))))
    assert len(out["errors"]) == 1, out
    assert out["errors"][0].endswith(": nodes nest at most 64 levels deep"), out


async def test_arming_fills_the_args_defaults(world):
    main = await world.spawn()
    text = (
        "id: d\ntitle: D\nsay: D.\n"
        "args: {type: object, properties: {n: {type: integer, default: 3}}}\n"
        "root:\n  wait: 1s\n  say: Wait.\n"
    )
    run = ok(await turn(main, mcp("workflow_arm", definition=text, args={})))["run"]
    assert world.app.workflows.runs[run].args == {"n": 3}
    assert world.app.workflows.wire(run)["args"] == {"n": 3}


NAP = (
    "id: nap\ntitle: Nap\nsay: Nap a while.\nroot:\n  seq:\n"
    "    - {wait: 1s, id: rest, say: Rest a second.}\n  id: all\n  say: Rest.\n"
)


async def test_a_person_reads_a_saved_workflow_and_an_agent_cannot(world):
    await world.app.registry.call("workflow.save", {"definition": NAP})
    got = await world.app.registry.call("workflow.read", {"id": "nap"})
    assert got["text"] == NAP and got["source"] == "project"
    assert got["steps"] == [
        {"say": "Rest.", "depth": 0},
        {"say": "Rest a second.", "depth": 1},
    ]
    a = await world.spawn()
    assert (await turn(a, mcp("workflow_read", id="nap"))).startswith("mcp error")


async def test_lint_names_the_id_of_a_workflow_that_lints(world):
    out = await world.app.registry.call("workflow.lint", {"definition": NAP})
    assert out == {"errors": [], "id": "nap"}
