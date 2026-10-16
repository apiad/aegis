"""/loop, the built-in workflow (spec §08): a person's line, the main agent's
rounds, and every way it ends."""

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_launch import TOKEN, launches, say


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def steps(main) -> list[str]:
    return [
        e.get("md", "")
        for e in main.entries()
        if e["kind"] == "inbox" and " · step rounds." in e.get("md", "")
    ]


async def start(world, line: str, args: dict):
    main = await world.spawn()
    await say(world, main, line)
    await until(lambda: launches(main), timeout=10)
    await until(lambda: main.status == "idle", timeout=10)
    tok = TOKEN.search(launches(main)[0]).group(1)
    run = ok(await turn(main, mcp("workflow_arm", id="loop", token=tok, args=args)))
    return main, run["run"]


async def answer_round(main, n: int, done: bool, next_: str | None = None):
    await until(lambda: len(steps(main)) >= n, timeout=15)
    await until(lambda: main.status == "idle", timeout=10)
    value = {"done": done} | ({"next": next_} if next_ else {})
    said = await turn(main, mcp("workflow_result", value=value, summary=f"Round {n}."))
    assert said.startswith("mcp ok"), said


@pytest.mark.slow
async def test_loop_runs_its_rounds_and_ends_done_when_they_run_out(world):
    main, run = await start(
        world,
        "/loop check the deploy every 1s, three times",
        {"prompt": "check the deploy", "every": "1s", "max": 3},
    )
    for n in (1, 2, 3):
        await answer_round(main, n, done=False)
    wf_ = world.app.workflows
    # Running out of rounds ends it done; a failure would wait with an open item.
    await until(
        lambda: wf_.runs[run].status == "done" or wf_.runs[run].open, timeout=15
    )
    st = wf_.runs[run]
    assert st.status == "done" and st.open == {}
    assert st.nodes["rounds"].round == 3
    assert st.launch["line"].startswith("/loop check the deploy")
    assert "check the deploy" in steps(main)[0]


@pytest.mark.slow
async def test_a_round_that_says_done_ends_the_loop_without_waiting(world):
    main, run = await start(
        world,
        "/loop check the deploy every 1h",
        {"prompt": "check the deploy", "every": "1h"},
    )
    await answer_round(main, 1, done=True)
    wf_ = world.app.workflows
    await until(lambda: wf_.runs[run].status == "done", timeout=10)  # no 1h wait
    st = wf_.runs[run]
    assert st.args["max"] == 20  # the default, filled at arm
    assert st.nodes["rounds.1.unless-done"].took == "none"


@pytest.mark.slow
async def test_without_every_the_agent_paces_itself(world):
    main, run = await start(
        world, "/loop tidy the inbox", {"prompt": "tidy the inbox", "max": 2}
    )
    await answer_round(main, 1, done=False, next_="1s")
    wf_ = world.app.workflows
    await until(
        lambda: (n := wf_.runs[run].nodes.get("rounds.1.pause")) and n.cmd == "1s",
        timeout=10,
    )
    await answer_round(main, 2, done=False, next_="1s")
    await until(lambda: wf_.runs[run].status == "done", timeout=15)


async def test_stop_ends_the_loop_and_frees_the_session_for_another(world):
    main, run = await start(
        world,
        "/loop check the deploy every 1h",
        {"prompt": "check the deploy", "every": "1h"},
    )
    with pytest.raises(OpError):
        await say(world, main, "/loop something else")  # one live run
    await world.app.registry.call("workflow.stop", {"run": run})
    await say(world, main, "/loop something else")
    await until(lambda: len(launches(main)) == 2, timeout=10)
