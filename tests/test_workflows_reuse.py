"""A step that runs in an earlier step's session (spec §04, §05)."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_parallel import report


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 1}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def reuse(first: str, second: str, third: str | None = None) -> str:
    more = (
        f"\n    - {{id: again, say: Once more., agent: {{session: fix, prompt: {json.dumps(third)}}}}}"
        if third
        else ""
    )
    return f"""id: reuse
title: Reuse
say: Write it, then fix it in the same session.
root:
  seq:
    - {{id: impl, say: Write it., agent: {{agent: opus, prompt: {json.dumps(first)}}}}}
    - {{id: fix, say: Fix it., agent: {{session: impl, prompt: {json.dumps(second)}}}}}{more}
  say: Both.
"""


def settled(wf, run: str) -> bool:
    """Ended, or a step failed: a failed step waits on the main agent."""
    st = wf.runs[run]
    return st.status != "running" or any(
        n.status == "failed" for n in st.nodes.values()
    )


async def run_it(world, text: str) -> str:
    a = await world.spawn()
    run = ok(await turn(a, mcp("workflow_arm", definition=text, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    return run


async def test_the_second_step_runs_in_the_first_ones_session(world):
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(2, 0.2), report(3, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    lids = {st.nodes[k].log_id for k in ("impl", "fix", "again")}
    assert len(lids) == 1
    # Archived after its last use, not before: the run is done and it is gone.
    assert next(iter(lids)) not in world.app.sessions.sessions


async def test_a_reused_session_takes_no_new_slot(world):
    # max_agents 1: a fresh session for fix would wait forever behind impl's.
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(2, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}


async def test_a_result_from_the_reused_session_finishes_the_later_step(world):
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(9, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["impl"].result == {"n": 1} and st.nodes["fix"].result == {"n": 9}
