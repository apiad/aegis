"""A cap counts every branch begun and not finished, against a real server
and fake agents: a branch blocked between two nodes on an after still holds
its slot."""

from .conftest import until
from .test_agents import CONFIG
from . import test_workflows_parallel
from .test_workflows_parallel import arm, leaf, overlap, report, script, spans

world = test_workflows_parallel.world  # the fixture, shared


async def test_a_branch_waiting_between_nodes_holds_its_slot(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 4}\n")
    give = script(
        ["sleep", 1.5],
        ["workflow_output", {"name": "o", "value": 1, "summary": "Gave o."}],
        ["sleep", 3],
        ["workflow_result", {"value": {"n": 1}, "summary": "Done x."}],
    )
    wf_text = f"""
id: holds
title: Holds
say: A capped par whose first branch waits partway on a node outside it.
root:
  par:
    - {leaf("x", "Give o.", give)}
    - par:
        - seq:
            - {leaf("a1", "First.", report(wait=0.3))}
            - {leaf("a2", "Second.", report(wait=1))}
          id: left
          say: Left.
        - {leaf("b", "Bee.", report(wait=2))}
      cap: 1
      id: capped
      say: One branch at a time.
  say: All.
""".replace("{id: a2,", "{after: [x.o], id: a2,")
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    left = spans(st, "a1") + spans(st, "a2")
    assert not overlap(left, spans(st, "b")), (left, spans(st, "b"))
