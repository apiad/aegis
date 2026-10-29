"""The card of a running run's main agent (#464): approving answers its
standing ask, the run is something it waits on, and it is neither nudged nor
kept up for it."""

import asyncio
import os
import re
import time
from datetime import datetime, timezone

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


async def armed(world, *steps: str) -> tuple:
    """A main agent that armed ``steps`` and asked for approval."""
    r = await world.app.registry.call("session.spawn", {"agent": "opus"})
    main = world.app.sessions.sessions[r["log_id"]]
    run = ok(await turn(main, mcp("workflow_arm", definition=flow(*steps), args={})))[
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
    mine = [c["attention"] for c in sent if c["log_id"] == main.log_id]
    assert mine and mine[0] == "waiting", mine
    # The approval then wakes it (#474); after that turn it still waits.
    await until(lambda: approved(main) and main.status == "idle", timeout=10)
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
    later = time.time() + 601
    attention = card(world, main)["attention"]
    assert (
        nudges.due(
            main.standing,
            attention=attention,
            worker=False,
            idle=True,
            now=later,
            silent_s=600.0,
            plan_s=900.0,
        )
        is None
    )
    f = world.app.parker.facts(main, deep=False)
    assert not {
        "its attention is waiting",
        "its last turn did not call turn_end",
    } & set(blockers(f))


def approved(s) -> list[str]:
    return [
        e.get("md") or ""
        for e in s.entries()
        if e["kind"] == "inbox" and "· approved ·" in (e.get("md") or "")
    ]


async def test_approving_tells_the_main_agent_what_comes_next(world):
    gate = '{gate: {by: person, ask: "Publish it?"}, say: Ask before publishing.}'
    main, run = await armed(world, "{wait: 30s, say: Pause.}", gate)
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: approved(main) and main.status == "idle", timeout=10)
    (md,) = approved(main)
    assert "> from workflow:steps · approved ·" in md and run in md
    assert 'turn_end(attention="review")' in md and "steer" in md
    assert '"Publish it?"' in md and "the person answers on the card" in md
    # Its reply is a turn after the click: still waiting on the run.
    await turn(main, mcp("turn_end", attention="review", line="It runs."))
    assert card(world, main)["attention"] == "waiting"


async def test_a_workflow_header_is_stamped_in_utc(world):
    # Off UTC, as on the smoke-test host of #758: local time without a zone
    # put the header four hours away from the notice that followed it.
    was = os.environ.get("TZ")
    os.environ["TZ"] = "America/Caracas"
    time.tzset()
    try:
        main, run = await armed(world, "{wait: 30s, say: Pause.}")
        await world.app.registry.call("workflow.approve", {"run": run})
        await until(lambda: approved(main), timeout=10)
    finally:
        if was is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = was
        time.tzset()
    (md,) = approved(main)
    m = re.search(r"· approved · (\S+)", md.splitlines()[0])
    assert m and m[1].endswith("Z"), md.splitlines()[0]
    at = datetime.strptime(m[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - at).total_seconds()) < 60


async def test_with_no_stop_on_the_way_the_next_call_is_the_end(world):
    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: approved(main), timeout=10)
    assert "when it ends" in approved(main)[0]


async def test_an_approval_is_told_once_across_a_restart(world, tmp_path):
    import json

    main, run = await armed(world, "{wait: 30s, say: Pause.}")
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: approved(main) and main.status == "idle", timeout=10)
    await world.restart()
    main = world.app.sessions.sessions[main.log_id]
    await asyncio.sleep(0.5)
    assert len(approved(main)) == 1
    # A crash between the delivery and its record: the next boot tells it.
    await world.stop()
    (log,) = tmp_path.rglob(f"{run}.jsonl")
    kept = [
        ln
        for ln in log.read_text().splitlines()
        if json.loads(ln).get("ev") != "approved_told"
    ]
    log.write_text("\n".join(kept) + "\n")
    await world.start()
    main = world.app.sessions.sessions[main.log_id]
    await until(lambda: len(approved(main)) == 2, timeout=10)


def test_the_next_stop_skips_what_finished():
    from aegis.workflow_lang import next_stop, parse

    wf, errors = parse(
        flow(
            '{gate: {by: main, ask: "Merge?"}, id: merge, say: Merge.}',
            "{agent: {prompt: Check., session: main}, id: check, say: I check.}",
        ),
        {"opus"},
    )
    assert not errors, errors
    assert next_stop(wf, set()).id == "merge"
    assert next_stop(wf, {"merge"}).id == "check"
    assert next_stop(wf, {"merge", "check"}) is None
