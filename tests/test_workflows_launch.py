"""Launch tokens (spec §06): a person's /workflow line, or an approved
schedule's fire, mints one; the main agent arms with it and the run starts
with no click. An agent can never mint, forge, carry or replay one, and
cannot change the definition or widen the args (spec §12)."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflow_store import BY, NAP
from .test_workflows_e2e import ok

PERSON = {"by": "person", "principal": "ana", "line": "/workflow nap one second"}


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.saved.save(NAP, w.app.workflows._agents(), BY)
    yield w
    await w.stop()


def mint(world, s, wid="nap", origin=PERSON) -> str:
    saved = world.app.saved.get(wid)
    return world.app.workflows.launches.issue(s.log_id, saved, "one second", origin)


async def test_a_launched_run_starts_without_a_click_and_says_who_launched_it(world):
    main = await world.spawn()
    tok = mint(world, main)
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"}))
    )
    st = world.app.workflows.runs[run["run"]]
    assert run["status"] == "running" and st.status == "running"
    assert st.launch["by"] == "person" and st.launch["typed"] == "one second"
    assert any(s["text"].startswith("Started by your /workflow line") for s in st.story)
    await until(
        lambda: world.app.workflows.runs[run["run"]].status == "done", timeout=10
    )


async def test_an_agent_cannot_forge_replay_or_carry_a_token(world):
    main, other = await world.spawn(), await world.spawn()
    args = {"secs": "1s"}
    said = await turn(
        main, mcp("workflow_arm", id="nap", token="made-up-token", args=args)
    )
    assert said.startswith("mcp error") and "did not issue" in said
    tok = mint(world, main)
    said = await turn(other, mcp("workflow_arm", id="nap", token=tok, args=args))
    assert said.startswith("mcp error") and "another session" in said
    run = ok(await turn(main, mcp("workflow_arm", id="nap", token=tok, args=args)))
    assert run["status"] == "running"  # the other session's try did not spend it
    await world.app.registry.call("workflow.stop", {"run": run["run"]})
    # The stop wakes the main agent first; its turn ends before the replay.
    await until(
        lambda: (
            main.status == "idle"
            and any(
                "· stopped ·" in e["md"] for e in main.entries() if e["kind"] == "prose"
            )
        )
    )
    said = await turn(main, mcp("workflow_arm", id="nap", token=tok, args=args))
    assert said.startswith("mcp error") and "already used" in said


async def test_a_token_is_for_one_workflow_and_its_args_stay_inside_the_schema(world):
    main = await world.spawn()
    tok = mint(world, main)
    said = await turn(
        main, mcp("workflow_arm", id="loop", token=tok, args={"prompt": "x"})
    )
    assert said.startswith("mcp error") and "for workflow nap" in said
    said = await turn(main, mcp("workflow_arm", definition=NAP, token=tok))
    assert said.startswith("mcp error") and "goes with id" in said
    wide = {"secs": "1s", "then": "rm -rf"}
    said = await turn(main, mcp("workflow_arm", id="nap", token=tok, args=wide))
    assert said.startswith("mcp error") and "$.args.then: not in the schema" in said
    # A refused arm did not spend it: the right args go through.
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"}))
    )
    assert run["status"] == "running"


async def test_the_run_is_the_definition_the_person_launched(world):
    main = await world.spawn()
    tok = mint(world, main)
    pinned = world.app.saved.get("nap").text
    # Between the line and the arm, an agent saves another version.
    world.app.saved.save(
        NAP.replace("Nap a while.", "Do something else."),
        world.app.workflows._agents(),
        BY,
    )
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"}))
    )
    st = world.app.workflows.runs[run["run"]]
    assert (
        st.text == pinned
        and world.app.workflows.wire(run["run"])["say"] == "Nap a while."
    )


async def test_a_token_dies_with_the_server(world):
    main = await world.spawn()
    tok = mint(world, main)
    world.app.workflows.launches._grants.clear()  # what a restart leaves
    said = await turn(
        main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"})
    )
    assert said.startswith("mcp error") and "type the /workflow line again" in said


async def test_a_token_expires_after_a_day(world):
    main = await world.spawn()
    tok = mint(world, main)
    launches = world.app.workflows.launches
    real = launches.clock
    launches.clock = lambda: real() + 86400 + 1
    said = await turn(
        main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"})
    )
    assert said.startswith("mcp error") and "it expired" in said


async def test_the_token_is_kept_nowhere_but_in_memory(world):
    main = await world.spawn()
    tok = mint(world, main)
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"}))
    )
    assert tok not in json.dumps(world.app.workflows.wire(run["run"]))
    # The agent's own transcript holds its call; the run's records do not.
    state = world.app.roots.state_root
    logs = [p.read_bytes() for p in (state / "workflow-runs").glob("*")]
    logs.append((state / "workflow-saves.jsonl").read_bytes())
    assert logs and not [b for b in logs if tok.encode() in b]
