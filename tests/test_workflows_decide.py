"""What the main agent decides when a step fails: the run waits on it,
other branches go on, and the run ends only on its answer."""

import pytest

from aegis.ops import Caller

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_parallel import leaf, report


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
