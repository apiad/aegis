"""The card of a running run's main agent (#464): approving answers its
standing ask, the run is something it waits on, and it is neither nudged nor
kept up for it."""

import time

import pytest

from aegis import nudges
from aegis.park import blockers

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_steps import flow

ASK = mcp("turn_end", attention="needs_you", line="Approve the workflow on its card?")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def armed(world, step: str) -> tuple:
    """A main agent that armed ``step`` and asked for approval."""
    r = await world.app.registry.call("session.spawn", {"agent": "opus"})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=flow(step), args={})))[
        "run"
    ]
    await turn(main, ASK)
    assert card(world, main)["attention"] == "needs_you"
    return main, run


def card(world, s) -> dict:
    return world.app.sessions.card(s)


@pytest.mark.parametrize(
    "step", ["{wait: 30s, say: Pause.}", '{sh: "sleep 30", say: Sleep.}']
)
async def test_approving_turns_the_owners_ask_into_waiting_on_the_run(world, step):
    main, run = await armed(world, step)
    sent: list[dict] = []
    publish = world.app.channels.publish

    def spy(channel, ops):
        if channel == "sessions":
            sent.extend(op["upsert"] for op in ops if "upsert" in op)
        publish(channel, ops)

    world.app.channels.publish = spy
    await world.app.registry.call("workflow.approve", {"run": run})
    # What a browser was sent, not only what a fresh read computes.
    mine = [c for c in sent if c["log_id"] == main.log_id]
    assert mine and mine[-1]["attention"] == "waiting"
    c = card(world, main)
    assert c["attention"] == "waiting", c
    assert f"workflow {run}" in c["waiting_on"]
    assert c["attention_line"] == "" and c["replies"] == []


async def test_an_armed_run_not_approved_still_needs_the_person(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    assert f"workflow {run}" not in card(world, main)["waiting_on"]


async def test_a_turn_during_the_run_falls_back_to_waiting(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    for said in ("review", "done"):
        await turn(main, mcp("turn_end", attention=said, line="Here is where it is."))
        assert card(world, main)["attention"] == "waiting"


async def test_a_real_ask_during_the_run_still_needs_you(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    await turn(main, mcp("turn_end", attention="needs_you", line="Which branch?"))
    c = card(world, main)
    assert c["attention"] == "needs_you" and c["attention_line"] == "Which branch?"


async def test_a_stopped_run_is_no_longer_waited_on(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    await world.app.registry.call("workflow.stop", {"run": run})
    # The stop tells the main agent, whose turn ends after it.
    await until(lambda: f"workflow {run}" not in card(world, main)["waiting_on"])


async def test_the_owner_of_a_running_run_is_not_nudged_and_may_park(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    await turn(main, "say nothing")  # a turn that ends without turn_end
    assert main.standing["ended"]["how"] == "silent"
    later = time.time() + nudges.IDLE_S + 1
    attention = card(world, main)["attention"]
    assert (
        nudges.due(
            main.standing, attention=attention, worker=False, idle=True, now=later
        )
        is None
    )
    f = world.app.parker.facts(main, deep=False)
    assert not {
        "its attention is waiting",
        "its last turn did not call turn_end",
    } & set(blockers(f))
