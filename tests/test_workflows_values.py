"""An agent's early outputs beside its result, against a real server and
fake agents: an output stays readable whatever the result holds, and a
result can meet an after on an output."""

from .conftest import until
from . import test_workflows_parallel
from .test_workflows_e2e import give_up
from .test_workflows_parallel import arm, leaf, report, script

world = test_workflows_parallel.world  # the fixture, shared


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


async def test_a_failure_story_line_reads_the_rendered_say(world):
    """A map whose over is no list, and a node whose after can never come,
    fail with their say rendered, not as templates."""
    bad_over = f"""
id: overs
title: Overs
say: A map over a number.
args: {{type: object}}
root:
  map: {{over: "{{{{args.n}}}}", as: x, do: {leaf("one", "One.", report())}}}
  id: items
  say: "Each of {{{{args.kind}}}}."
"""
    never = f"""
id: nevers
title: Nevers
say: A wait on an item that is not there.
args: {{type: object}}
root:
  par:
    - map: {{over: [{{id: a}}], as: y, do: {leaf("two", "Two.", report(wait=0.1))}}}
      id: others
      say: Others.
    - {leaf("late", "Late for {{args.kind}}.", report(wait=0.1))}
  say: All.
""".replace("    - {id: late,", "    - {after: [others.zz], id: late,")
    wf = world.app.workflows
    texts = []
    for text in (bad_over, never):
        run = await arm(world, text, {"n": 3, "kind": "cats"})
        await give_up(world, run, 40)
        texts += [s["text"] for s in wf.runs[run].story]
    assert any(t.startswith("Each of cats. Failed: over is a int") for t in texts), (
        texts
    )
    assert any(t.startswith("Late for cats. Failed: it waits on") for t in texts), texts
    assert not any("{{" in t for t in texts), texts


async def test_a_steps_see_renders_per_item_and_hides_until_it_can(world):
    wf_text = f"""
id: seen
title: Seen
say: Each slice, with its part of the plan.
root:
  seq:
    - {{sh: "sleep 0.5", id: first, say: First.}}
    - map: {{over: [a, b], as: s, do: {{sh: "true", id: t, say: "Do {{{{s}}}}.", see: "plan.html#slice-{{{{s}}}}"}}}}
      id: m
      say: Each.
  say: Both.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    rows = {r["id"]: r for r in wf.wire(run)["tree"]}
    assert rows["t"]["each"] and "see" not in rows["t"]  # once, before the map
    await until(lambda: wf.runs[run].status != "running", timeout=20)
    rows = {r["id"]: r for r in wf.wire(run)["tree"]}
    assert [rows[f"m.{i}.t"]["see"] for i in (0, 1)] == [
        "plan.html#slice-a",
        "plan.html#slice-b",
    ]
