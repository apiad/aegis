"""Par and map against a real server and fake agents: caps, the pool, after
and early outputs, and a restart in the middle of a map."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import give_up, ok


def script(*steps) -> str:
    return "/script " + json.dumps(list(steps))


def report(n: int = 7, wait: float = 1.0) -> str:
    return script(
        ["sleep", wait],
        ["workflow_result", {"value": {"n": n}, "summary": f"Did it, n is {n}."}],
    )


def leaf(id_: str, say: str, prompt: str, extra: str = "", agent: str = "opus") -> str:
    return f"{{id: {id_}, say: {json.dumps(say)}, agent: {{agent: {agent}, prompt: {json.dumps(prompt)}{extra}}}}}"


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


@pytest.mark.slow
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


@pytest.mark.slow
async def test_a_failure_beside_a_running_branch_lets_it_finish(world):
    wf_text = f"""
id: sides
title: Sides
say: Two branches; one fails.
root:
  par:
    - {leaf("slow", "Take a while.", report(wait=3))}
    - {leaf("bad", "Fail.", script(["sleep", 1]))}
  say: Both.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows

    def slow_done():
        n = wf.runs[run].nodes.get("slow")
        return n is not None and n.status == "done"

    await until(slow_done, timeout=40)
    st = wf.runs[run]
    assert st.status == "running" and st.nodes["bad"].status == "failed"
    st = await give_up(world, run)
    assert st.status == "failed"


@pytest.mark.slow
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


def two_maps(a: int, b: int) -> str:
    return f"""
id: wide
title: Two maps
say: Two maps over more items than the pool holds.
root:
  par:
    - map: {{over: {json.dumps(list(range(a)))}, as: x, do: {leaf("one", "Left {{x}}.", report())}}}
      id: left
      say: Left.
    - map: {{over: {json.dumps(list(range(b)))}, as: y, do: {leaf("two", "Right {{y}}.", report())}}}
      id: right
      say: Right.
  say: Both maps.
"""


@pytest.mark.slow
async def test_two_maps_never_have_more_than_max_agents_sessions_live(world):
    """Spec §12. The pool is max_agents: 2 (the fixture's .aegis.yaml); six
    items want six sessions at once. Every spawn is counted the moment it
    returns, so a peak above two cannot slip between two samples."""
    reg = world.app.sessions
    peak, real = 0, reg.spawn

    async def counting(*a, **k):
        nonlocal peak
        s = await real(*a, **k)
        peak = max(peak, sum(1 for x in reg.sessions.values() if x.workflow))
        return s

    reg.spawn = counting
    run = await arm(world, two_maps(3, 3))
    wf = world.app.workflows
    seen_queued = False

    def settled():
        nonlocal seen_queued
        seen_queued |= any(r["state"] == "queued" for r in wf.wire(run)["tree"])
        return wf.runs[run].status != "running"

    await until(settled, timeout=60)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sum(k.endswith((".one", ".two")) for k in st.nodes) == 6
    assert peak == 2, peak
    assert seen_queued


# The fixture's agents plus haiku, so a run mixes two aliases.
TWO = CONFIG.replace(
    "agents:\n",
    "agents:\n  haiku: {harness: claude-code, model: haiku, effort: low, permission: full}\n",
    1,
)


def peaks(world) -> dict:
    """Wrap the registry's spawn so each node spawn records how many node
    sessions are open, in all and per alias, the moment it returns."""
    reg, real = world.app.sessions, world.app.sessions.spawn
    peak: dict = {}

    async def counting(*a, **k):
        s = await real(*a, **k)
        nodes = [x for x in reg.sessions.values() if x.workflow]
        for key in ("total", *{x.spec.agent for x in nodes}):
            n = (
                len(nodes)
                if key == "total"
                else sum(x.spec.agent == key for x in nodes)
            )
            peak[key] = max(peak.get(key, 0), n)
        return s

    reg.spawn = counting
    return peak


@pytest.mark.slow
async def test_the_server_bounds_one_alias_while_another_runs_beside_it(world):
    (world.root / ".aegis.yaml").write_text(
        TWO + "workflows: {max_agents: {total: 4, opus: 1}}\n"
    )
    peak = peaks(world)
    wf_text = f"""
id: aliases
title: Aliases
say: Two opus steps and one haiku step.
root:
  par:
    - {leaf("o1", "Opus one.", report())}
    - {leaf("o2", "Opus two.", report())}
    - {leaf("h1", "Haiku one.", report(wait=3), agent="haiku")}
  say: All three.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert peak["opus"] == 1 and peak["total"] == 2, peak
    assert not overlap(spans(st, "o1"), spans(st, "o2"))
    assert overlap(spans(st, "o1"), spans(st, "h1"))


@pytest.mark.slow
async def test_limits_bound_a_run_in_all_and_per_alias(world):
    (world.root / ".aegis.yaml").write_text(TWO + "workflows: {max_agents: 4}\n")
    peak = peaks(world)
    wf_text = f"""
id: limited
title: Limited
say: Two haiku steps and two opus steps, two at a time.
limits: {{total: 2, haiku: 1}}
root:
  par:
    - {leaf("h1", "Haiku one.", report(), agent="haiku")}
    - {leaf("h2", "Haiku two.", report(), agent="haiku")}
    - {leaf("o1", "Opus one.", report())}
    - {leaf("o2", "Opus two.", report())}
  say: All four.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=60)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert peak["total"] == 2 and peak["haiku"] == 1, peak
    assert not overlap(spans(st, "h1"), spans(st, "h2"))


async def test_a_map_waits_on_one_item_of_another(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 4}\n")
    wf_text = f"""
id: teams
title: Teams
say: The mock team starts once the base image is built.
root:
  par:
    - map: {{over: [{{id: base}}, {{id: svc}}], as: s, cap: 1, do: {leaf("img", "Build {{s.id}}.", report())}}}
      id: images
      say: Images, one at a time.
    - map: {{over: [{{id: m1}}], as: m, do: {leaf("mk", "Mock {{m.id}}.", report())}}}
      id: mock
      after: [images.base]
      say: Mocks, after the base.
  say: Both teams.
"""
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done"
    base, svc, mk = (
        st.nodes[k] for k in ("images.base.img", "images.svc.img", "mock.m1.mk")
    )
    assert base.ended <= mk.started < svc.ended


@pytest.mark.slow
async def test_a_node_starts_on_an_early_output_and_reads_it(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 4}\n")
    probe = script(
        [
            "workflow_output",
            {"name": "timers", "value": [3, 5], "summary": "Found two timers."},
        ],
        ["workflow_output", {"name": "timers", "value": [], "summary": "Again."}],
        ["sleep", 3],
        ["workflow_result", {"value": {"n": 1}, "summary": "Probed."}],
    )
    wf_text = f"""
id: early
title: Early
say: A probe and a step that needs only part of it.
root:
  par:
    - {leaf("probe", "Probe the timers.", probe)}
    - {leaf("use", "Use timers {{probe.timers}}.", report(wait=0.1), "")}
  say: Both.
""".replace("    - {id: use,", "    - {after: [probe.timers], id: use,")
    run = await arm(world, wf_text)
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=40)
    st = wf.runs[run]
    assert st.status == "done"
    assert st.nodes["probe"].outputs == {
        "timers": [3, 5]
    }  # the second call was refused
    assert st.nodes["use"].started < st.nodes["probe"].ended
    assert st.nodes["use"].say == "Use timers [3, 5]."
    assert any(
        s["text"] == "Probe the timers. Early output timers: Found two timers."
        for s in st.story
    )


async def test_an_after_that_can_never_come_fails_the_waiting_node(world):
    wf_text = f"""
id: never
title: Never
say: A wait on an item that is not there.
root:
  par:
    - map: {{over: [{{id: a}}], as: x, do: {leaf("one", "Item {{x.id}}.", report(wait=0.1))}}}
      id: items
      say: Items.
    - {leaf("late", "After a missing item.", report(wait=0.1))}
  say: Both.
""".replace("    - {id: late,", "    - {after: [items.zz], id: late,")
    run = await arm(world, wf_text)
    st = await give_up(world, run, 30)
    assert st.status == "failed"
    assert "map items has no item zz" in st.nodes["late"].error
