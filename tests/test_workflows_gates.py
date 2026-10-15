"""Gates against a real server: the main agent's, the person's, and that
no agent can press the person's button."""

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def gated(by: str) -> str:
    return (
        "id: gated\ntitle: Gated\nsay: Gated.\nroot:\n  seq:\n"
        '    - {sh: "printf 41", id: a, say: Count.}\n'
        f'    - {{gate: {{by: {by}, ask: "Go on with {{{{a.out}}}}?", show: [a.out]}}, id: g, say: Ask.}}\n'
        '    - {sh: "test {{g.value}} = 42", id: use, say: Use the value.}\n'
        "  say: Three.\n"
    )


async def armed(world, by: str):
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=gated(by), args={})))[
        "run"
    ]
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: "g" in world.app.workflows.runs[run].open, timeout=20)
    return main, run


async def test_a_main_gate_wakes_the_main_agent_and_its_value_is_read(world):
    main, run = await armed(world, "main")
    wf = world.app.workflows
    await until(lambda: wf.runs[run].open["g"]["told"], timeout=10)
    assert wf.runs[run].nodes["g"].gate["shown"] == {"a.out": "41"}
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "workflow.gate", {"run": run, "node": "g", "go": True}
        )
    assert e.value.code == "not_yours_to_answer"  # a main gate is the main agent's
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "g", "choice": "continue", "value": 42},
        Caller("agent", main.log_id),
    )
    await until(lambda: wf.runs[run].status == "done", timeout=20)


async def test_a_person_gate_shows_a_card_and_never_wakes_the_main_agent(world):
    main, run = await armed(world, "person")
    wf = world.app.workflows
    cards = [e for e in main.entries() if e["kind"] == "workflow_decision"]
    assert [c["detail"] for c in cards] == [{"run": run, "what": "gate", "key": "g@0"}]
    assert not [
        e
        for e in main.entries()
        if e["kind"] == "inbox" and " · gate · " in e.get("md", "")
    ]
    await world.app.registry.call(
        "workflow.gate", {"run": run, "node": "g", "go": True, "answer": "42"}
    )
    await until(lambda: wf.runs[run].status == "done", timeout=20)


async def test_no_agent_answers_a_person_gate(world):
    # Spec §12: this must fail if workflow.gate is marked for agents.
    main, run = await armed(world, "person")
    said = await turn(main, mcp("workflow_gate", run=run, node="g", go=True))
    assert said.startswith("mcp error"), said
    with pytest.raises(OpError):
        await world.app.registry.call(
            "workflow.gate",
            {"run": run, "node": "g", "go": True},
            Caller("agent", main.log_id),
        )
    said = await turn(main, mcp("workflow_answer", run=run, to="g", choice="continue"))
    assert said.startswith("mcp error") and "person" in said
    assert world.app.workflows.runs[run].nodes["g"].status == "running"


async def test_stop_at_a_person_gate_stops_the_run(world):
    main, run = await armed(world, "person")
    await world.app.registry.call(
        "workflow.gate", {"run": run, "node": "g", "go": False, "answer": "Not today."}
    )
    st = world.app.workflows.runs[run]
    assert st.status == "stopped" and any("Not today." in s["text"] for s in st.story)


def person_gate(step: str) -> str:
    return (
        "id: pg\ntitle: Pg\nsay: Pg.\nroot:\n  seq:\n"
        '    - {sh: "printf 41", id: a, say: Count.}\n'
        f"    - {step}\n"
        "  say: Two.\n"
    )


async def failed_gate(world, definition: str, failed: str):
    """Arm and approve a run, and wait until ``failed`` waits on the main
    agent as a failure."""
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=definition, args={})))[
        "run"
    ]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(
        lambda: (wf.runs[run].open.get(failed) or {}).get("kind") == "failed",
        timeout=20,
    )
    return main, run


async def refused_skip(world, main, run: str, to: str) -> None:
    """Skip is refused, with or without a value, and retry reopens the gate
    to the person."""
    wf, me = world.app.workflows, Caller("agent", main.log_id)
    for value in (None, {"value": 42}):
        with pytest.raises(OpError) as e:
            await world.app.registry.call(
                "workflow.answer",
                {"run": run, "to": to, "choice": "skip", "value": value},
                me,
            )
        assert e.value.code == "bad_choice" and "person" in e.value.message
    assert wf.runs[run].nodes["g"].status != "skipped"
    text = wf._wake_text(wf.runs[run], to, wf.runs[run].open[to])
    assert '"skip"' not in text and '"retry"' in text and '"fail"' in text


async def test_the_main_agent_cannot_stop_a_person_gate(world):
    main, run = await armed(world, "person")
    wf = world.app.workflows
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "workflow.stop", {"run": run, "node": "g"}, Caller("agent", main.log_id)
        )
    assert e.value.code == "not_running" and "person" in e.value.message
    assert wf.runs[run].open["g"]["kind"] == "gate"


async def test_a_person_gate_past_its_deadline_is_retried_never_skipped(world):
    gate = '{gate: {by: person, ask: "ok?", show: []}, id: g, say: Ask., deadline: 1s}'
    main, run = await failed_gate(world, person_gate(gate), "g")
    await refused_skip(world, main, run, "g")
    wf = world.app.workflows
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "g", "choice": "retry"},
        Caller("agent", main.log_id),
    )
    await until(lambda: wf.runs[run].open.get("g", {}).get("kind") == "gate", 10)
    assert wf.runs[run].open["g"]["by"] == "person"


async def test_a_group_past_its_deadline_around_a_person_gate_is_never_skipped(
    world,
):
    group = (
        '{seq: [{gate: {by: person, ask: "ok?", show: []}, id: g, say: Ask.}],'
        " id: grp, say: Grp., deadline: 1s}"
    )
    main, run = await failed_gate(world, person_gate(group), "grp")
    await refused_skip(world, main, run, "grp")


async def test_a_person_gate_that_did_not_render_is_never_skipped(world):
    gate = '{gate: {by: person, ask: "Go on with {{a.nope}}?", show: []}, id: g, say: Ask.}'
    main, run = await failed_gate(world, person_gate(gate), "g")
    await refused_skip(world, main, run, "g")


async def test_a_reopened_person_gate_asks_on_a_new_card(world):
    gate = '{gate: {by: person, ask: "ok?", show: []}, id: g, say: Ask., deadline: 1s}'
    main, run = await failed_gate(world, person_gate(gate), "g")
    wf = world.app.workflows
    before = [e["id"] for e in main.entries()]
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "g", "choice": "retry"},
        Caller("agent", main.log_id),
    )
    await until(lambda: wf.runs[run].open.get("g", {}).get("kind") == "gate", 10)
    cards = [e for e in main.entries() if e["kind"] == "workflow_decision"]
    assert [c["detail"]["key"] for c in cards] == ["g@0", "g@1"]
    # The new card sits below every entry the transcript held at the retry
    # (one held then may be gone), not at the old card's place.
    ids = [e["id"] for e in main.entries()]
    new = ids.index(cards[1]["id"])
    assert all(ids.index(x) < new for x in before if x in ids)
