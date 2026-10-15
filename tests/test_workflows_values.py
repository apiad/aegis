"""An agent's early outputs beside its result, against a real server and
fake agents: an output stays readable whatever the result holds, and a
result can meet an after on an output."""

from .conftest import until
from .test_workflows_parallel import arm, leaf, report, script, world  # noqa: F401


async def test_an_early_output_outlives_a_result_that_is_not_an_object(world):
    give = script(
        ["workflow_output", {"name": "o", "value": "early", "summary": "Gave o."}],
        ["workflow_result", {"value": "whole", "summary": "Done a."}],
    )
    wf_text = f"""
id: keep
title: Keep
say: An output, then a string result.
root:
  seq:
    - {leaf("a", "Give o.", give, extra=", result: {type: string}")}
    - {leaf("b", "Read {{a.o}} and {{a}}.", report(wait=0.1))}
  say: Both.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["b"].say == "Read early and whole."


async def test_an_early_output_wins_over_a_result_key_of_its_name(world):
    give = script(
        ["workflow_output", {"name": "o", "value": "early", "summary": "Gave o."}],
        ["workflow_result", {"value": {"o": "late", "n": 1}, "summary": "Done a."}],
    )
    wf_text = f"""
id: wins
title: Wins
say: An output, then a result with a key of its name.
root:
  seq:
    - {leaf("a", "Give o.", give)}
    - {leaf("b", "Read {{a.o}} and {{a.n}}.", report(wait=0.1))}
  say: Both.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["b"].say == "Read early and 1."


async def test_a_result_key_meets_an_after_on_an_output(world):
    """The agent never called workflow_output; its result holds o."""
    give = script(
        ["sleep", 0.5],
        ["workflow_result", {"value": {"o": 3}, "summary": "Done a."}],
    )
    wf_text = f"""
id: late
title: Late
say: A wait on an output that comes in the result.
root:
  par:
    - {leaf("a", "Give o late.", give)}
    - {leaf("b", "Use {{a.o}}.", report(wait=0.1))}
  say: Both.
""".replace("    - {id: b,", "    - {after: [a.o], id: b,")
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["b"].say == "Use 3."
