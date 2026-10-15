"""The engine under faults: a run's exception, a stop racing a pass, a
handoff of the main agent, a crash between spawn and log, a restart that
finds a definition no longer lints (review of slice 1)."""

import asyncio

import pytest

from aegis.agents import resolve
from aegis.workflow_log import RunLog

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import RESULT, flow, ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def told(main, outcome: str = "") -> list[str]:
    """The owner's inbox turns from the workflow, ``outcome`` ones only if given."""
    return [
        e["md"]
        for e in main.entries()
        if e["kind"] == "inbox"
        and "> from workflow:" in (e.get("md") or "")
        and f"· {outcome}" in e["md"]
    ]


def terminal(main) -> list[str]:
    return [
        m
        for m in told(main)
        if any(f"· {o} ·" in m for o in ("done", "failed", "stopped"))
    ]


async def armed(world, main, definition: str) -> str:
    run = ok(await turn(main, mcp("workflow_arm", definition=definition, args={})))[
        "run"
    ]
    await world.app.registry.call("workflow.approve", {"run": run})
    return run


async def started(world, run: str, node: str = "one"):
    wf = world.app.workflows
    await until(
        lambda: wf.runs[run].nodes.get(node) and wf.runs[run].nodes[node].log_id,
        timeout=10,
    )
    return world.app.sessions.sessions[wf.runs[run].nodes[node].log_id]


BADCWD = """
id: badcwd
title: Bad
say: A node whose cwd does not exist.
root:
  agent: {agent: opus, prompt: hi, cwd: /definitely/not/here}
  say: Do it.
"""


async def test_a_cwd_that_is_not_there_is_refused_at_arm(world):
    main = await world.spawn()
    said = await turn(main, mcp("workflow_arm", definition=BADCWD, args={}))
    assert said.startswith("mcp error") and "not/here" in said, said
    assert world.app.workflows.of_owner(main.log_id) is None


async def test_a_run_whose_node_cannot_start_fails_and_the_next_run_goes_on(world):
    a, b = await world.spawn(), await world.spawn()
    wf, reg = world.app.workflows, world.app.sessions
    ra = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    real = reg.spawn

    async def spawn(*args, **kw):
        if (kw.get("workflow") or {}).get("run") == ra:
            raise RuntimeError("no harness today")
        return await real(*args, **kw)

    reg.spawn = spawn
    rb = await armed(world, b, flow(RESULT, RESULT))
    await world.app.registry.call("workflow.approve", {"run": ra})
    await until(lambda: wf.runs[ra].status == "failed", timeout=10)
    assert "no harness today" in wf.runs[ra].nodes["one"].error
    await until(lambda: told(a, "failed"), timeout=10)
    await until(lambda: wf.runs[rb].status == "done", timeout=20)


async def test_a_wake_during_a_pass_starts_another_pass(world):
    wf = world.app.workflows
    calls: list[int] = []
    real = wf.step

    async def step():
        calls.append(1)
        if len(calls) == 1:
            wf.changed()  # a hook fires while this pass runs
            await asyncio.sleep(0.05)
        await real()

    wf.step = step
    wf.changed()
    await until(lambda: len(calls) >= 2, timeout=3, what="a second pass")


async def test_a_stop_during_a_nodes_spawn_leaves_no_node_working(world):
    main = await world.spawn()
    wf, reg = world.app.workflows, world.app.sessions
    real = reg.spawn

    async def slow(*args, **kw):
        await asyncio.sleep(0.5)
        return await real(*args, **kw)

    reg.spawn = slow
    run = await armed(world, main, flow("/sleep 3", RESULT))
    await asyncio.sleep(0.1)  # the pass is inside the spawn
    await world.app.registry.call("workflow.stop", {"run": run})
    assert wf.runs[run].status == "stopped"
    await asyncio.sleep(1)
    nodes = [s for s in reg.sessions.values() if (s.workflow or {}).get("run") == run]
    assert all(s.status != "working" and not s.quiet for s in nodes), [
        (s.status, s.quiet) for s in nodes
    ]
    await until(lambda: terminal(main), timeout=10)
    await asyncio.sleep(0.5)
    assert len(terminal(main)) == 1 and "· stopped ·" in terminal(main)[0]


async def test_a_stop_during_the_last_close_is_the_runs_one_ending(world):
    main = await world.spawn()
    wf, reg = world.app.workflows, world.app.sessions
    real = reg.close

    async def slow(log_id, *args, **kw):
        await asyncio.sleep(0.5)
        return await real(log_id, *args, **kw)

    reg.close = slow
    run = await armed(world, main, flow(RESULT, RESULT))
    await until(
        lambda: (
            wf.runs[run].nodes.get("two") and wf.runs[run].nodes["two"].status == "done"
        ),
        timeout=20,
    )
    try:
        await world.app.registry.call("workflow.stop", {"run": run})
    except Exception:
        pass  # the run may already be over: either ending is fine, not both
    await until(lambda: terminal(main), timeout=10)
    await asyncio.sleep(1)
    endings = [
        r
        for r in RunLog(wf.dir / f"{run}.jsonl").read()
        if r["ev"] in ("stopped", "finished")
    ]
    assert len(endings) == 1, endings
    assert len(terminal(main)) == 1, terminal(main)
    assert wf.runs[run].status in ("done", "stopped")


async def test_a_stopped_runs_node_gets_its_tab_and_keeps_it(world):
    main = await world.spawn()
    run = await armed(world, main, flow("/sleep 2", RESULT))
    s = await started(world, run)
    assert s.quiet
    await world.app.registry.call("workflow.stop", {"run": run})
    assert not s.quiet
    await until(lambda: s.status != "working", timeout=10)
    await asyncio.sleep(0.5)
    assert not s.quiet


async def handed_off(world, main):
    """Hand ``main`` off and wait until its successor has archived it."""
    reg = world.app.sessions
    new = await world.app.registry.call("session.handoff", {"log_id": main.log_id})
    await until(lambda: main.log_id not in reg.sessions, timeout=15)
    return reg.sessions[new["log_id"]]


async def test_a_run_follows_its_main_agent_through_a_handoff(world):
    main = await world.spawn()
    wf = world.app.workflows
    run = await armed(world, main, flow("/sleep 8", RESULT))
    one = await started(world, run)
    heir = await handed_off(world, main)
    assert wf.owner(wf.runs[run]) is heir and one.status == "working"
    wf.result(one, {"n": 7}, "Did it.")
    wf.changed()
    await until(lambda: wf.runs[run].status != "running", timeout=20)
    assert wf.runs[run].status == "done", wf.runs[run].nodes
    await until(lambda: told(heir, "done"), timeout=10)


async def test_the_heir_of_a_main_agent_may_stop_its_run(world):
    main = await world.spawn()
    run = await armed(world, main, flow("/sleep 3", RESULT))
    await started(world, run)
    heir = await handed_off(world, main)
    said = await turn(heir, mcp("workflow_stop", run=run))
    assert said.startswith("mcp ok"), said
    assert world.app.workflows.runs[run].status == "stopped"


REVIEWED = """
id: reviewed
title: Reviewed
say: One review.
root:
  agent: {agent: reviewer, prompt: "/sleep 10"}
  say: Review it.
"""


async def test_a_run_that_no_longer_lints_at_boot_tells_its_owner(world, tmp_path):
    main = await world.spawn()
    run = await armed(world, main, REVIEWED)
    node = await started(world, run, "n1")
    (tmp_path / ".aegis.yaml").write_text(
        "\n".join(ln for ln in CONFIG.splitlines() if "reviewer" not in ln) + "\n"
    )
    await world.restart()
    wf, reg = world.app.workflows, world.app.sessions
    assert wf.runs[run].status == "failed"
    main = reg.sessions[main.log_id]
    await until(lambda: told(main, "failed"), timeout=10)
    assert "no longer lints" in told(main, "failed")[0]
    assert not reg.sessions[node.log_id].quiet


async def test_a_node_session_spawned_before_a_crash_is_adopted_not_doubled(world):
    main = await world.spawn()
    wf, reg = world.app.workflows, world.app.sessions
    run = ok(
        await turn(main, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    # What a crash between the spawn and its node_started record leaves behind.
    agents, default = world.app._agents()
    spec = resolve(agents, default, "opus", {}, main.spec.cwd, spawned_by=main.log_id)
    orphan = await reg.spawn(
        spec, title="x", quiet=True, workflow={"run": run, "node": "one"}
    )
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    assert wf.runs[run].nodes["one"].log_id == orphan.log_id
    tagged = [
        lid
        for lid, m in list(reg.archived.items())
        if (m.get("workflow") or {}).get("node") == "one"
    ] + [
        s.log_id
        for s in reg.sessions.values()
        if (s.workflow or {}).get("node") == "one"
    ]
    assert tagged == [orphan.log_id], tagged


async def test_a_session_a_node_spawned_cannot_arm_a_workflow(world):
    node = await world.spawn()
    node.workflow = {"run": "r1", "node": "x"}  # r1 is not live: the node talks
    reg = world.app.sessions
    said = await turn(node, mcp("session_spawn", agent="opus", prompt="hi"))
    await until(
        lambda: any(s.spec.spawned_by == node.log_id for s in reg.sessions.values()),
        timeout=10,
    )
    child = next(s for s in reg.sessions.values() if s.spec.spawned_by == node.log_id)
    await until(lambda: child.status != "working", timeout=10)
    said = await turn(
        child, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={})
    )
    assert said.startswith("mcp error") and "nested" in said, said
    # Still so once the node is archived: its meta names it.
    await reg.close(node.log_id)
    said = await turn(
        child, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={})
    )
    assert said.startswith("mcp error") and "nested" in said, said
    assert world.app.workflows.of_owner(child.log_id) is None
