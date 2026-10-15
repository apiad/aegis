"""A workflow from arm to done against a real server and fake agents."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


def flow(first: str, second: str) -> str:
    return f"""
id: two-steps
title: Two steps
say: Do one thing, then another.
root:
  seq:
    - agent: {{agent: opus, prompt: {json.dumps(first)},
              result: {{type: object, properties: {{n: {{type: integer}}}}, required: [n]}}}}
      id: one
      say: Do the first thing.
    - agent: {{agent: opus, prompt: {json.dumps(second)}}}
      id: two
      say: Do the second thing.
  say: Both things.
"""


RESULT = mcp("workflow_result", value={"n": 7}, summary="Did it, n is 7.")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def test_a_workflow_runs_after_a_person_approves_it(world):
    main = await world.spawn()
    armed = ok(
        await turn(main, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )
    run = armed["run"]
    assert armed["status"] == "armed"
    assert [e["kind"] for e in main.entries()].count("workflow") == 1
    said = await turn(main, mcp("workflow_approve", run=run))
    assert said.startswith("mcp error"), said  # the tool is not served to agents
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    st = wf.runs[run]
    assert st.nodes["one"].result == {"n": 7}
    assert [r["state"] for r in wf.wire(run)["tree"]] == ["done", "done", "done"]
    assert [s["mark"] for s in st.story] == ["ok", "ok", "ok"]
    for n in st.nodes.values():
        assert n.log_id not in world.app.sessions.sessions  # archived
    await until(
        lambda: any(
            "> from workflow:two-steps · done" in e.get("md", "")
            for e in main.entries()
            if e["kind"] == "inbox"
        ),
        timeout=10,
    )


async def test_a_node_that_ends_without_a_result_is_nudged_then_fails_the_run(world):
    main = await world.spawn()
    armed = ok(
        await turn(
            main,
            mcp("workflow_arm", definition=flow("no result here", RESULT), args={}),
        )
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    wf = world.app.workflows
    await until(lambda: wf.runs[armed["run"]].status == "failed", timeout=20)
    one = wf.runs[armed["run"]].nodes["one"]
    assert one.nudged and "no workflow_result" in one.error
    assert (
        wf.runs[armed["run"]].nodes.get("two") is None
        or wf.runs[armed["run"]].nodes["two"].status == "pending"
    )


async def test_a_result_that_breaks_its_schema_is_refused(world):
    main = await world.spawn()
    bad = mcp("workflow_result", value={"n": "seven"}, summary="Did it.")
    armed = ok(
        await turn(main, mcp("workflow_arm", definition=flow(bad, RESULT), args={}))
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    wf = world.app.workflows
    await until(
        lambda: (
            wf.runs[armed["run"]].nodes.get("one")
            and wf.runs[armed["run"]].nodes["one"].log_id
        ),
        timeout=10,
    )
    node = world.app.sessions.sessions[wf.runs[armed["run"]].nodes["one"].log_id]
    await until(
        lambda: any(
            "$.n: expected integer"
            in (e.get("md") or "") + json.dumps(e.get("detail") or {})
            for e in node.entries()
        ),
        timeout=10,
    )


async def test_a_restart_mid_run_finishes_the_run(world):
    main = await world.spawn()
    armed = ok(
        await turn(
            main, mcp("workflow_arm", definition=flow("/sleep 2", RESULT), args={})
        )
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    await until(
        lambda: world.app.workflows.runs[armed["run"]].nodes.get("one"), timeout=10
    )
    await world.restart()
    wf = world.app.workflows
    run = armed["run"]
    assert wf.runs[run].status == "running"
    one = world.app.sessions.sessions[wf.runs[run].nodes["one"].log_id]
    wf.result(one, {"n": 7}, "Did it after the restart.")
    wf.changed()
    await until(lambda: wf.runs[run].status != "running", timeout=30)
    assert wf.runs[run].status == "done", {
        k: (v.status, v.error) for k, v in wf.runs[run].nodes.items()
    }
