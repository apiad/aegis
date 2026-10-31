"""Live edits against a real server: the diff waits for a person, nodes
it touches wait with it, and no agent applies its own edit."""

import asyncio
import json
import time

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import approve, ok
from .test_workflows_steps import verdict

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


async def armed(world, agent: str = "opus", definition: str = BASE, settle=True):
    r = await world.app.registry.call("session.spawn", {"agent": agent})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=definition, args={})))[
        "run"
    ]
    if settle:
        await approve(world, run)
    else:  # its step's timing matters more than the approval's turn
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
    await world.app.registry.call("workflow.edit.apply", {"run": run, "v": 2})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    assert (world.root / "notes").exists() and wf.runs[run].version == 2
    await until(lambda: woke(main, " · edit · v2 · "), timeout=10)
    assert "applied your change v2" in woke(main, " · edit · v2 · ")[0]


async def test_no_agent_applies_or_rejects_an_edit(world):
    # Spec §12: this must fail if either op is marked for agents.
    main, run = await armed(world)
    ok(await turn(main, mcp("workflow_edit", run=run, definition=WITH_NOTES)))
    for tool in ("workflow_edit_apply", "workflow_edit_reject"):
        assert (await turn(main, mcp(tool, run=run, v=2))).startswith("mcp error")
    for op in ("workflow.edit.apply", "workflow.edit.reject"):
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
    await world.app.registry.call("workflow.edit.reject", {"run": run, "v": 2})
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


COUNTED = (
    "id: again\ntitle: Again\nsay: Again.\nroot:\n  seq:\n"
    '    - {sh: "echo x >> ran", id: once, say: Once.}\n'
    '    - {sh: "sleep 30", id: long, say: Long.}\n'
    "  say: Two.\n"
)


async def test_arming_a_stopped_run_again_keeps_what_ran(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=COUNTED, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("long") or 0)
            and wf.runs[run].nodes["long"].status == "running"
        ),
        timeout=20,
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    quick = COUNTED.replace("sleep 30", "true")
    out = ok(await turn(main, mcp("workflow_arm", run=run, definition=quick)))
    assert out["run"] == run and wf.runs[run].status == "armed"
    assert wf.runs[run].version == 2
    assert [
        e["detail"]["what"] for e in main.entries() if e["kind"] == "workflow_decision"
    ] == ["again"]
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    assert (world.root / "ran").read_text() == "x\n"  # once ran once


async def test_arming_again_may_not_change_what_ran(world):
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=COUNTED, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("long") or 0)
            and wf.runs[run].nodes["long"].status == "running"
        ),
        timeout=20,
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    said = await turn(
        main,
        mcp("workflow_arm", run=run, definition=COUNTED.replace("echo x", "echo y")),
    )
    assert said.startswith("mcp error") and "has run and keeps its result" in said
    said = await turn(main, mcp("workflow_arm", run=run, args={"k": 1}))
    assert said.startswith("mcp error") and "keeps its args" in said
    assert wf.runs[run].status == "stopped"


async def test_only_a_stopped_or_failed_run_of_ones_own_is_armed_again(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=COUNTED, args={})))["run"]
    said = await turn(main, mcp("workflow_arm", run=run))
    assert said.startswith("mcp error") and "is armed" in said
    other = await world.spawn()
    await world.app.registry.call("workflow.reject", {"run": run})
    said = await turn(other, mcp("workflow_arm", run=run))
    assert said.startswith("mcp error")
    # The rejection wakes main; its answer must not read as the next.
    await until(
        lambda: woke(main, " · rejected · ") and main.status == "idle", timeout=10
    )
    said = await turn(main, mcp("workflow_arm"))
    assert said.startswith("mcp error")


@pytest.mark.slow
async def test_arming_again_starts_a_groups_deadline_afresh(world):
    grp = (
        '    - {seq: [{sh: "true", id: x, say: X.}, {sh: "exit 3", id: y, say: Y.}],'
        " id: grp, say: Grp., deadline: 2s}\n"
    )
    definition = "id: dl\ntitle: Dl\nsay: Dl.\nroot:\n  seq:\n" + grp + "  say: One.\n"
    main, run = await armed(world, definition=definition)
    wf, me = world.app.workflows, Caller("agent", main.log_id)
    await until(lambda: "y" in wf.runs[run].open, timeout=10)
    await world.app.registry.call(
        "workflow.answer", {"run": run, "to": "y", "choice": "fail"}, me
    )
    await until(lambda: wf.runs[run].status == "failed", timeout=10)
    # Past the group's deadline counted from its first start.
    await until(lambda: time.time() > wf.runs[run].nodes["x"].started + 2.5, 10)
    await world.app.registry.call("workflow.arm", {"run": run, "args": {}}, me)
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: {"y", "grp"} & set(wf.runs[run].open), timeout=10)
    assert "grp" not in wf.runs[run].open, wf.runs[run].open
    assert wf.runs[run].nodes["y"].error.startswith("exit 3")


@pytest.mark.slow  # waits out real deadlines
async def test_finished_steps_never_expire_after_an_edit(world):
    # Two steps and a group finish early; a change is applied; the clock
    # passes their deadlines while the run waits on a later step.
    definition = (
        "id: dl\ntitle: Dl\nsay: Dl.\nroot:\n  seq:\n"
        '    - {sh: "true", id: r0, deadline: 2s, say: R0.}\n'
        '    - {seq: [{sh: "true", id: a, say: A.}], id: plan, deadline: 2s, say: Plan.}\n'
        "    - {wait: 30s, id: hold, say: Hold.}\n"
        "  id: top\n  say: Three.\n"
    )
    main, run = await armed(world, definition=definition)
    wf = world.app.workflows
    await until(lambda: "hold" in wf.runs[run].nodes, timeout=10)
    later = definition.replace(
        "  id: top\n", '    - {sh: "true", id: tail, say: Tail.}\n  id: top\n'
    )
    ok(await turn(main, mcp("workflow_edit", run=run, definition=later)))
    await world.app.registry.call("workflow.edit.apply", {"run": run, "v": 2})
    st = wf.runs[run]
    await until(lambda: time.time() > st.nodes["r0"].started + 3.5, timeout=10)
    assert st.status == "running" and not st.open, st.open
    for k in ("r0", "a", "hold"):
        assert st.nodes[k].status in ("done", "running"), (k, st.nodes[k].error)
    assert tree(wf, run)["plan"]["state"] == "done"


async def test_arming_again_with_a_command_needs_full_permission(world):
    waits = (
        "id: ed\ntitle: Edit\nsay: Edit.\nroot:\n  seq:\n"
        # The edits touch only the step after: a stop can land before or after
        # the run's first step has started, and a started step cannot change.
        "    - {wait: 30s, id: first, say: First.}\n"
        "    - {wait: 30s, id: build, say: Build.}\n"
        "  say: One.\n"
    )
    main, run = await armed(world, agent="writer", definition=waits, settle=False)
    wf = world.app.workflows
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    shelled = waits.replace("{wait: 30s, id: build", '{sh: "make", id: build')
    with pytest.raises(OpError) as e:
        wf.arm(main, shelled, {}, run=run)
    assert e.value.code == "not_allowed" and "full permission" in e.value.message
    # A change the session may make is armed with its diff on the story line.
    later = waits.replace("wait: 30s, id: build", "wait: 1s, id: build")
    wf.arm(main, later, {}, run=run)
    line = wf.runs[run].story[-1]
    assert line["text"].startswith("Armed again")
    assert [(d["op"], d["key"]) for d in line["diff"]] == [("change", "build")]
    card = [e for e in main.entries() if e["kind"] == "workflow_decision"][-1]
    assert wf.runs[run].story[int(card["detail"]["key"])] is line


async def test_a_change_is_not_rejected_once_the_run_ended(world):
    main, run = await armed(world)
    wf = world.app.workflows
    await until(lambda: "build" in wf.runs[run].nodes, timeout=10)
    out = await world.app.registry.call(
        "workflow.edit",
        {"run": run, "definition": WITH_NOTES},
        Caller("agent", main.log_id),
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    for op in ("workflow.edit.reject", "workflow.edit.apply"):
        with pytest.raises(OpError) as e:
            await world.app.registry.call(op, {"run": run, "v": out["proposed"]})
        assert e.value.code == "not_proposed", op
    assert wf.runs[run].edits[-1]["status"] == "dropped"  # not "rejected"


async def test_arming_again_gives_a_step_its_retries_back(world):
    once = (
        "id: rt\ntitle: Rt\nsay: Rt.\nroot:\n  seq:\n"
        '    - {sh: "exit 3", id: bad, retries: 1, say: Fail.}\n'
        "  say: One.\n"
    )
    main, run = await armed(world, definition=once)
    wf, me = world.app.workflows, Caller("agent", main.log_id)

    def retrying() -> list[str]:
        return [
            s["text"] for s in wf.runs[run].story if "Retrying, attempt" in s["text"]
        ]

    await until(lambda: "bad" in wf.runs[run].open, timeout=10)
    await world.app.registry.call(
        "workflow.answer", {"run": run, "to": "bad", "choice": "fail"}, me
    )
    await until(lambda: wf.runs[run].status == "failed", timeout=10)
    assert len(retrying()) == 1
    await world.app.registry.call("workflow.arm", {"run": run, "args": {}}, me)
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: "bad" in wf.runs[run].open, timeout=10)
    assert retrying() == ["Fail. Failed: exit 3 Retrying, attempt 2 of 2."] * 2


async def test_a_change_proposed_before_a_stop_is_dropped(world):
    waits = (
        "id: ed\ntitle: Edit\nsay: Edit.\nroot:\n  seq:\n"
        "    - {wait: 30s, id: build, say: Build.}\n"
        "  say: One.\n"
    )
    main, run = await armed(world, definition=waits)
    wf = world.app.workflows
    me = Caller("agent", main.log_id)
    await until(lambda: "build" in wf.runs[run].nodes, timeout=10)
    more = waits.replace(
        "  say: One.\n", "    - {wait: 1s, id: more, say: More.}\n  say: One.\n"
    )
    out = await world.app.registry.call(
        "workflow.edit", {"run": run, "definition": more}, me
    )
    await world.app.registry.call("workflow.stop", {"run": run}, me)
    await until(lambda: wf.runs[run].status == "stopped", timeout=10)
    assert wf.wire(run)["edits"][-1]["status"] == "dropped"
    await world.app.registry.call("workflow.arm", {"run": run, "args": {}}, me)
    await world.app.registry.call("workflow.approve", {"run": run})
    w = wf.wire(run)
    assert w["status"] == "running" and w["proposal"] is None
    assert w["edits"][-1]["status"] == "dropped"  # no live buttons come back
    with pytest.raises(OpError):
        await world.app.registry.call(
            "workflow.edit.apply", {"run": run, "v": out["proposed"]}
        )


async def test_a_step_moved_into_a_begun_seq_holds_the_step_after_it(world):
    two = (
        "id: ed\ntitle: Edit\nsay: Edit.\nroot:\n  seq:\n"
        "    - seq:\n"
        "        - {wait: 30s, id: build, say: Build.}\n"
        '        - {sh: "true", id: ship, say: Ship.}\n'
        "      id: first\n      say: First.\n"
        "    - seq:\n"
        '        - {sh: "true", id: notes, say: Write notes.}\n'
        '        - {sh: "true", id: last, say: Last.}\n'
        "      id: later\n      say: Later.\n"
        "  say: Two.\n"
    )
    moved = two.replace(
        '        - {sh: "true", id: notes, say: Write notes.}\n', ""
    ).replace(
        '        - {sh: "true", id: ship, say: Ship.}\n',
        '        - {sh: "true", id: notes, say: Write notes.}\n'
        '        - {sh: "true", id: ship, say: Ship.}\n',
    )
    main, run = await armed(world, definition=two)
    wf = world.app.workflows
    await until(lambda: "build" in wf.runs[run].nodes, timeout=10)
    await world.app.registry.call(
        "workflow.edit", {"run": run, "definition": moved}, Caller("agent", main.log_id)
    )
    st = wf.runs[run]
    assert [d["op"] for d in st.proposal["diff"] if d["key"] == "notes"] == ["move"]
    # Run now, ship would go before the step moved in ahead of it.
    assert "ship" in st.proposal["touched"]


def probed(when: str) -> str:
    return (
        "id: probed\ntitle: Probed\nsay: Probed.\nroot:\n  seq:\n"
        f'    - {{if: {{when: {when}, then: {{sh: "echo y >> took", id: "yes", say: Yes.}}}}, id: c, say: Probe.}}\n'
        "  say: One.\n"
    )


@pytest.mark.slow
@pytest.mark.parametrize("probe", ["sh", "judge"])
async def test_an_if_stopped_while_probing_probes_again_when_armed_again(world, probe):
    j = json.dumps(verdict(True, wait=3))
    when = (
        '{sh: "sleep 3 && true"}'
        if probe == "sh"
        else f"{{judge: {{agent: opus, prompt: {j}}}}}"
    )
    main = await world.spawn()
    d = probed(when)
    run = ok(await turn(main, mcp("workflow_arm", definition=d, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows

    def asking():
        n = wf.runs[run].nodes.get("c")
        return (run, "c") in wf._tasks or (n and n.judges.get("", {}).get("log_id"))

    await until(asking, timeout=10)
    await asyncio.sleep(0.3)  # the probe is in flight
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    ok(await turn(main, mcp("workflow_arm", run=run)))
    assert wf.runs[run].nodes["c"].status != "stopped"
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: wf.runs[run].status in ("done", "failed"), timeout=20)
    assert wf.runs[run].status == "done"
    assert (world.root / "took").read_text() == "y\n"


async def test_a_run_rejected_when_armed_again_keeps_its_steps_and_arms_again(world):
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=COUNTED, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("long") or 0)
            and wf.runs[run].nodes["long"].status == "running"
        ),
        timeout=20,
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    quick = COUNTED.replace("sleep 30", "true")
    ok(await turn(main, mcp("workflow_arm", run=run, definition=quick)))
    await world.app.registry.call("workflow.reject", {"run": run})
    await until(
        lambda: woke(main, " · rejected · ") and main.status == "idle", timeout=10
    )
    (md,) = woke(main, " · rejected · ")
    assert "steps it finished are kept" in md and f'run="{run}"' in md
    assert "nothing ran" not in md
    ok(await turn(main, mcp("workflow_arm", run=run, definition=quick)))
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    assert (world.root / "ran").read_text() == "x\n"  # once ran once


async def test_arming_again_after_a_reject_shows_the_rejected_change_again(world):
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=COUNTED, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    wf = world.app.workflows
    await until(
        lambda: (
            (wf.runs[run].nodes.get("long") or 0)
            and wf.runs[run].nodes["long"].status == "running"
        ),
        timeout=20,
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    await until(
        lambda: woke(main, " · stopped · ") and main.status == "idle", timeout=10
    )
    quick = COUNTED.replace("sleep 30", "echo z >> other")
    ok(await turn(main, mcp("workflow_arm", run=run, definition=quick)))
    first = wf.runs[run].story[-1]
    await world.app.registry.call("workflow.reject", {"run": run})
    await until(
        lambda: woke(main, " · rejected · ") and main.status == "idle", timeout=10
    )
    # Armed again as it stands, the rejected text is still a change to
    # what the person approved, and the card says so as the first time.
    ok(await turn(main, mcp("workflow_arm", run=run)))
    line = wf.runs[run].story[-1]
    assert line["text"].startswith("Armed again")
    assert line.get("diff") and line["diff"] == first["diff"]
    assert line.get("commands") == first["commands"]
    assert line["commands"]["sh"] == 1
    assert any(d.get("cmd") == "echo z >> other" for d in line["diff"])
    assert wf.runs[run].version == 2
    await world.app.registry.call("workflow.reject", {"run": run})
    await until(
        lambda: len(woke(main, " · rejected · ")) == 2 and main.status == "idle",
        timeout=10,
    )
    # Armed again with what was approved, there is nothing to show.
    ok(await turn(main, mcp("workflow_arm", run=run, definition=COUNTED)))
    line = wf.runs[run].story[-1]
    assert not line.get("diff") and wf.runs[run].version == 1
