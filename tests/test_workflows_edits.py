"""Live edits against a real server: the diff waits for a person, nodes
it touches wait with it, and no agent applies its own edit."""

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok

WRITER = (
    "  writer: {harness: claude-code, model: opus, effort: high, permission: write}\n"
)


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(
        CONFIG.replace("queues:\n", WRITER + "queues:\n")
    )
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


BASE = (
    "id: ed\ntitle: Edit\nsay: Edit.\nroot:\n  seq:\n"
    '    - {sh: "sleep 1 && echo built", id: build, say: Build.}\n'
    '    - {sh: "true", id: ship, say: Ship.}\n'
    "  say: Two.\n"
)
WITH_NOTES = BASE.replace(
    '    - {sh: "true", id: ship, say: Ship.}\n',
    '    - {sh: "touch notes", id: notes, say: Write notes.}\n    - {sh: "true", id: ship, say: Ship.}\n',
)


async def armed(world, agent: str = "opus", definition: str = BASE):
    r = await world.app.registry.call("session.spawn", {"agent": agent})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=definition, args={})))[
        "run"
    ]
    await world.app.registry.call("workflow.approve", {"run": run})
    return main, run


def woke(main, word: str) -> list[str]:
    return [
        e.get("md") or ""
        for e in main.entries()
        if e["kind"] == "inbox" and word in (e.get("md") or "")
    ]


def tree(wf, run) -> dict:
    return {r["id"]: r for r in wf.wire(run)["tree"]}


async def test_an_edit_waits_for_the_person_and_then_runs_the_new_step(world):
    main, run = await armed(world)
    ok(await turn(main, mcp("workflow_edit", run=run, definition=WITH_NOTES)))
    wf = world.app.workflows
    st = wf.runs[run]
    assert st.proposal["v"] == 2 and [d["say"] for d in st.proposal["diff"]] == [
        "Write notes."
    ]
    assert [
        e["detail"]["what"] for e in main.entries() if e["kind"] == "workflow_decision"
    ] == ["edit"]
    await until(
        lambda: (
            (wf.runs[run].nodes.get("build") or 0)
            and wf.runs[run].nodes["build"].status == "done"
        ),
        timeout=20,
    )
    # The new step goes before ship, so ship waits for the answer too.
    await until(lambda: tree(wf, run)["ship"].get("held"), timeout=10)
    assert "ship" not in wf.runs[run].nodes
    await world.app.registry.call("workflow.apply_edit", {"run": run, "v": 2})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    assert (world.root / "notes").exists() and wf.runs[run].version == 2
    await until(lambda: woke(main, " · edit · v2 · "), timeout=10)
    assert "applied your change v2" in woke(main, " · edit · v2 · ")[0]


async def test_no_agent_applies_or_rejects_an_edit(world):
    # Spec §12: this must fail if either op is marked for agents.
    main, run = await armed(world)
    ok(await turn(main, mcp("workflow_edit", run=run, definition=WITH_NOTES)))
    for tool in ("workflow_apply_edit", "workflow_reject_edit"):
        assert (await turn(main, mcp(tool, run=run, v=2))).startswith("mcp error")
    for op in ("workflow.apply_edit", "workflow.reject_edit"):
        with pytest.raises(OpError):
            await world.app.registry.call(
                op, {"run": run, "v": 2}, Caller("agent", main.log_id)
            )
    assert world.app.workflows.runs[run].proposal is not None


async def test_an_edit_that_adds_a_command_needs_full_permission(world):
    waits = (
        "id: ed\ntitle: Edit\nsay: Edit.\nroot:\n  seq:\n"
        "    - {wait: 5s, id: build, say: Build.}\n"
        "    - {wait: 1s, id: ship, say: Ship.}\n"
        "  say: Two.\n"
    )
    main, run = await armed(world, agent="writer", definition=waits)
    noted = waits.replace(
        "    - {wait: 1s, id: ship",
        '    - {sh: "touch notes", id: notes, say: Write notes.}\n    - {wait: 1s, id: ship',
    )
    said = await turn(main, mcp("workflow_edit", run=run, definition=noted))
    assert said.startswith("mcp error") and "full permission" in said, said


async def test_an_edit_may_not_change_what_ran(world):
    main, run = await armed(world)
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("build") or 0)
            and wf.runs[run].nodes["build"].status == "running"
        ),
        timeout=20,
    )
    said = await turn(
        main,
        mcp(
            "workflow_edit", run=run, definition=BASE.replace("echo built", "echo made")
        ),
    )
    assert (
        said.startswith("mcp error")
        and "is running: stop it with workflow_stop" in said
    ), said
    said = await turn(main, mcp("workflow_edit", run=run, definition=BASE))
    assert said.startswith("mcp error") and "the definition is the one running" in said


async def test_a_touched_node_waits_for_the_answer(world):
    main, run = await armed(world)
    changed = BASE.replace('{sh: "true", id: ship', '{sh: "true && true", id: ship')
    ok(await turn(main, mcp("workflow_edit", run=run, definition=changed)))
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("build") or 0)
            and wf.runs[run].nodes["build"].status == "done"
        ),
        timeout=20,
    )
    await world.app.workflows.step()  # a pass after build, which would start ship
    assert tree(wf, run)["ship"].get("held")
    assert "ship" not in wf.runs[run].nodes  # not started while the edit waits
    await world.app.registry.call("workflow.reject_edit", {"run": run, "v": 2})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    await until(lambda: woke(main, " · edit · v2 · "), timeout=10)
    assert "kept the plan" in woke(main, " · edit · v2 · ")[0]
    assert wf.runs[run].version == 1 and wf.runs[run].edits[-1]["status"] == "rejected"


async def test_arming_again_before_approval_replaces_the_card(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"})
    main = world.app.sessions.sessions[r["log_id"]]
    first = ok(await turn(main, mcp("workflow_arm", definition=BASE, args={})))["run"]
    second = ok(await turn(main, mcp("workflow_arm", definition=WITH_NOTES, args={})))[
        "run"
    ]
    wf = world.app.workflows
    assert wf.runs[first].status == "replaced" and wf.runs[first].replaced_by == second
    assert wf.runs[second].status == "armed" and wf.runs[second].version == 2
    assert wf.wire(first)["replaced_by"] == second
