"""Par and map against a real server and fake agents: caps, the pool, after
and early outputs, and a restart in the middle of a map."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok


def script(*steps) -> str:
    return "/script " + json.dumps(list(steps))


def report(n: int = 7, wait: float = 1.0) -> str:
    return script(
        ["sleep", wait],
        ["workflow_result", {"value": {"n": n}, "summary": f"Did it, n is {n}."}],
    )


def leaf(id_: str, say: str, prompt: str, extra: str = "") -> str:
    return f"{{id: {id_}, say: {json.dumps(say)}, agent: {{agent: opus, prompt: {json.dumps(prompt)}{extra}}}}}"


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 2}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def arm(world, definition: str, args: dict | None = None) -> str:
    main = await world.spawn()
    armed = ok(
        await turn(main, mcp("workflow_arm", definition=definition, args=args or {}))
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    return armed["run"]


def spans(st, prefix: str) -> list[tuple[float, float]]:
    return sorted(
        (n.started, n.ended)
        for k, n in st.nodes.items()
        if k.startswith(prefix) and n.started
    )


def overlap(a: list, b: list) -> bool:
    return any(s1 < e2 and s2 < e1 for s1, e1 in a for s2, e2 in b)


async def test_a_map_with_cap_one_runs_its_items_in_turn(world):
    wf_text = f"""
id: turns
title: Turns
say: Three items, one at a time.
root:
  map: {{over: [{{id: a}}, {{id: b}}, {{id: c}}], as: x, cap: 1,
         do: {leaf("one", "Item {{x.id}}.", report())}}}
  id: items
  say: Each item in turn.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sorted(k for k in st.nodes if k.endswith(".one")) == [
        "items.a.one",
        "items.b.one",
        "items.c.one",
    ]
    s = spans(st, "items.")
    assert all(e1 <= s2 for (_, e1), (s2, _) in zip(s, s[1:]))  # never two at once
    assert st.nodes["items.b.one"].say == "Item b."
    tree = wf.wire(run)["tree"]
    assert [r["id"] for r in tree] == [
        "items",
        "items.a.one",
        "items.b.one",
        "items.c.one",
    ]
    assert tree[0]["count"] == {"done": 3}


async def test_a_failure_beside_a_running_branch_stops_it(world):
    wf_text = f"""
id: sides
title: Sides
say: Two branches; one fails.
root:
  par:
    - {leaf("slow", "Take long.", script(["sleep", 30]))}
    - {leaf("bad", "Fail.", script(["sleep", 1]))}
  say: Both.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "failed"
    assert (st.nodes["bad"].status, st.nodes["slow"].status) == ("failed", "stopped")
    slow = world.app.sessions.sessions[st.nodes["slow"].log_id]
    await until(lambda: slow.status != "working", timeout=5)  # interrupted


async def test_a_restart_mid_map_keeps_its_items_and_its_cap(world):
    wf_text = f"""
id: again
title: Again
say: Three items through a restart.
args: {{type: object, properties: {{xs: {{type: array}}}}}}
root:
  map: {{over: "{{{{args.xs}}}}", as: x, cap: 1, do: {leaf("one", "Item {{x}}.", report(wait=2))}}}
  id: items
  say: Each item in turn.
"""
    run = await arm(world, wf_text, {"xs": ["p", "q", "r"]})
    wf = world.app.workflows
    await until(lambda: wf.runs[run].nodes.get("items.0.one"), timeout=10)
    await world.restart()
    wf = world.app.workflows
    first = world.app.sessions.sessions[wf.runs[run].nodes["items.0.one"].log_id]
    wf.result(first, {"n": 1}, "Did it after the restart.")
    wf.changed()
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    lines = (
        (world.app.roots.state_root / "workflow-runs" / f"{run}.jsonl")
        .read_text()
        .splitlines()
    )
    assert sum('"map_items"' in ln for ln in lines) == 1
    s = spans(st, "items.")
    assert len(s) == 3 and all(e1 <= s2 for (_, e1), (s2, _) in zip(s, s[1:]))
