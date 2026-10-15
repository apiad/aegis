"""What the main agent decides when a step fails: the run waits on it,
other branches go on, and the run ends only on its answer."""

import asyncio

import pytest

from aegis.ops import Caller
from aegis.workflow_log import NodeState

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_parallel import leaf, report, script


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 3}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def wf(body: str) -> str:
    return f"id: dec\ntitle: Decide\nsay: Decide.\nroot:\n{body}\n"


SIDE_BY_SIDE = wf(
    "  par:\n"
    f"    - {leaf('bad', 'Fail at once.', '/exit 3')}\n"
    f"    - {leaf('slow', 'Take a while.', report(wait=3))}\n"
    "  say: Both."
)


async def armed(world, definition: str):
    main = await world.spawn()
    run = ok(await turn(main, mcp("workflow_arm", definition=definition, args={})))[
        "run"
    ]
    await world.app.registry.call("workflow.approve", {"run": run})
    return main, run


def inbox(main, word: str) -> list[str]:
    return [
        e.get("md", "")
        for e in main.entries()
        if e["kind"] == "inbox" and f" · {word} · " in e.get("md", "")
    ]


@pytest.mark.slow
async def test_a_failure_wakes_the_main_agent_and_the_other_branch_finishes(world):
    main, run = await armed(world, SIDE_BY_SIDE)
    wf_ = world.app.workflows
    await until(
        lambda: (
            wf_.runs[run].nodes.get("slow")
            and wf_.runs[run].nodes["slow"].status == "done"
        ),
        timeout=30,
    )
    st = wf_.runs[run]
    assert st.status == "running"
    assert st.nodes["bad"].status == "failed" and st.open["bad"]["told"] is True
    await until(lambda: inbox(main, "failed"), timeout=10)
    told = inbox(main, "failed")[0]
    assert "Fail at once." in told and 'to="bad"' in told
    assert len(inbox(main, "failed")) == 1


async def test_only_the_main_agent_ends_the_run_with_fail(world):
    main, run = await armed(
        world, wf(f"  seq:\n    - {leaf('bad', 'Fail.', '/exit 3')}\n  say: One.")
    )
    wf_ = world.app.workflows
    await until(lambda: "bad" in wf_.runs[run].open, timeout=20)
    other = await world.spawn()
    said = await turn(other, mcp("workflow_answer", run=run, to="bad", choice="fail"))
    assert said.startswith("mcp error") and "only the session that armed" in said
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "bad", "choice": "fail", "text": "Nothing to salvage."},
        Caller("agent", main.log_id),
    )
    st = wf_.runs[run]
    assert st.status == "failed" and st.open == {}
    assert any("Nothing to salvage." in s["text"] for s in st.story)


@pytest.mark.slow
async def test_the_runs_deadline_ends_it_without_asking(world):
    body = f"  seq:\n    - {leaf('slow', 'Sleep.', '/sleep 20')}\n  say: One."
    main, run = await armed(world, wf(body).replace("root:", "deadline: 2s\nroot:"))
    wf_ = world.app.workflows
    await until(lambda: wf_.runs[run].status != "running", timeout=10)
    assert wf_.runs[run].status == "failed" and wf_.runs[run].open == {}


async def test_a_wake_is_told_again_only_when_it_was_never_told(world):
    main, run = await armed(
        world, wf(f"  seq:\n    - {leaf('bad', 'Fail.', '/exit 3')}\n  say: One.")
    )
    wf_ = world.app.workflows
    await until(lambda: wf_.runs[run].open.get("bad", {}).get("told"), timeout=20)
    st = wf_.runs[run]
    await wf_._tell_open(st)  # nothing untold: no second message
    await until(lambda: inbox(main, "failed"), timeout=5)
    assert len(inbox(main, "failed")) == 1


async def test_retry_runs_the_failed_step_again_and_the_run_finishes(world, tmp_path):
    marker = tmp_path / "second"
    sh = f'{{sh: "test -e {marker} || {{ touch {marker}; exit 1; }}", id: flaky, say: Flaky.}}'
    main, run = await armed(
        world,
        wf(
            f'  seq:\n    - {sh}\n    - {{sh: "true", id: after, say: After.}}\n  say: Two.'
        ),
    )
    wf_ = world.app.workflows
    await until(lambda: "flaky" in wf_.runs[run].open, timeout=20)
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "flaky", "choice": "retry", "text": "It was a flake."},
        Caller("agent", main.log_id),
    )
    await until(lambda: wf_.runs[run].status == "done", timeout=20)
    assert wf_.runs[run].nodes["flaky"].attempts == 1


async def test_skip_gives_later_steps_its_value(world):
    bad = leaf(
        "impl",
        "Implement.",
        "/exit 3",
        extra=", result: {type: object, properties: {pr: {type: integer}}, required: [pr]}",
    )
    use = '{sh: "test {{impl.pr}} = 9", id: use, say: Use it.}'
    main, run = await armed(world, wf(f"  seq:\n    - {bad}\n    - {use}\n  say: Two."))
    wf_ = world.app.workflows
    await until(lambda: "impl" in wf_.runs[run].open, timeout=20)
    owner = Caller("agent", main.log_id)
    with pytest.raises(Exception, match="schema"):
        await world.app.registry.call(
            "workflow.answer",
            {"run": run, "to": "impl", "choice": "skip", "value": {"pr": "nine"}},
            owner,
        )
    await world.app.registry.call(
        "workflow.answer",
        {
            "run": run,
            "to": "impl",
            "choice": "skip",
            "value": {"pr": 9},
            "text": "Merged by hand.",
        },
        owner,
    )
    await until(lambda: wf_.runs[run].status == "done", timeout=20)
    rows = {r["id"]: r["state"] for r in wf_.wire(run)["tree"]}
    assert rows["impl"] == "skipped" and rows["use"] == "done"


async def test_the_main_agent_stops_one_node_and_the_run_waits(world):
    body = f"  par:\n    - {leaf('long', 'Work long.', '/sleep 30')}\n    - {leaf('ok', 'Finish.', report(wait=1))}\n  say: Both."
    main, run = await armed(world, wf(body))
    wf_ = world.app.workflows
    await until(
        lambda: (wf_.runs[run].nodes.get("long") or NodeState()).status == "running",
        timeout=20,
    )
    said = await turn(main, mcp("workflow_stop", run=run, node="long"))
    assert said.startswith("mcp ok")
    st = wf_.runs[run]
    assert st.status == "running" and st.nodes["long"].status == "failed"
    assert (
        st.open["long"]["told"] is True
        and "stopped by the main agent" in st.nodes["long"].error
    )
    s = world.app.sessions.sessions[st.nodes["long"].log_id]
    await until(lambda: s.status != "working", timeout=10)


ASKS = script(
    ["workflow_ask", {"question": "Which branch, main or next?"}],
)


async def test_a_worker_asks_ends_its_turn_and_finishes_after_the_answer(world):
    main, run = await armed(
        world, wf(f"  seq:\n    - {leaf('w', 'Work.', ASKS)}\n  say: One.")
    )
    wf_ = world.app.workflows
    await until(
        lambda: "q1" in wf_.runs[run].open and wf_.runs[run].open["q1"]["told"],
        timeout=20,
    )
    s = world.app.sessions.sessions[wf_.runs[run].nodes["w"].log_id]
    await until(lambda: s.status != "working", timeout=10)
    assert not wf_.runs[run].nodes["w"].nudged  # an open question is not a silent turn
    # The fake reads the answer's first line as its next command.
    answer = script(["workflow_result", {"value": {}, "summary": "Used main."}])
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "q1", "text": answer},
        Caller("agent", main.log_id),
    )
    await until(lambda: wf_.runs[run].status == "done", timeout=20)
    assert any(x["mark"] == "ask" for x in wf_.runs[run].story)


async def test_only_a_node_asks_and_only_the_main_agent_notes(world):
    main, run = await armed(
        world, wf(f"  seq:\n    - {leaf('w', 'Work.', report(wait=5))}\n  say: One.")
    )
    said = await turn(main, mcp("workflow_ask", question="?"))
    assert said.startswith("mcp error") and "ask the person" in said
    said = await turn(
        main, mcp("workflow_note", run=run, text="Chose main because the plan says so.")
    )
    assert said.startswith("mcp ok")
    other = await world.spawn()
    assert (await turn(other, mcp("workflow_note", run=run, text="x"))).startswith(
        "mcp error"
    )
    assert world.app.workflows.runs[run].story[-1]["mark"] == "note"


MAIN = (
    "id: m\ntitle: M\nsay: M.\nroot:\n  seq:\n"
    '    - {agent: {session: main, prompt: "Check the deploy.", result: {type: object, properties: {done: {type: boolean}}, required: [done]}}, id: tick, say: Ask the main agent.}\n'
    '    - {sh: "test {{tick.done}} = true", id: after, say: After.}\n'
    "  say: Two.\n"
)


async def test_a_session_main_step_is_the_main_agents_and_spawns_nothing(world):
    open_before = set(world.app.sessions.sessions)
    main, run = await armed(world, MAIN)
    open_before.add(main.log_id)
    wf_ = world.app.workflows
    await until(
        lambda: any(" · step tick · " in (e.get("md") or "") for e in main.entries()),
        timeout=20,
    )
    await until(lambda: main.status == "idle", timeout=10)
    said = await turn(
        main,
        mcp("workflow_result", value={"done": True}, summary="The deploy is green."),
    )
    assert said.startswith("mcp ok"), said
    await until(lambda: wf_.runs[run].status == "done", timeout=20)
    assert set(world.app.sessions.sessions) <= open_before  # nothing was spawned
    tick = wf_.runs[run].nodes["tick"]
    assert tick.main and tick.log_id is None


async def test_stopping_a_run_never_interrupts_the_main_agent_at_its_step(world):
    long = MAIN.replace('prompt: "Check the deploy."', 'prompt: "/sleep 4"')
    main, run = await armed(world, long)
    wf_ = world.app.workflows
    await until(
        lambda: (
            (n := wf_.runs[run].nodes.get("tick")) is not None
            and n.status == "running"
            and main.status == "working"
        ),
        timeout=20,
    )
    await world.app.registry.call("workflow.stop", {"run": run})
    await asyncio.sleep(0.5)
    assert main.status == "working"  # still at its step: the stop is the run's
    assert not [e for e in main.entries() if e["kind"] == "interrupt"]
