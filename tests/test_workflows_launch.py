"""Launch tokens (spec §06): a person's /workflow line, or an approved
schedule's fire, mints one; the main agent arms with it and the run starts
with no click. An agent can never mint, forge, carry or replay one, and
cannot change the definition or widen the args (spec §12)."""

import json
import re

import pytest

from aegis.ops import Caller, OpError

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


TOKEN = re.compile(r'token="([^"]+)"')


def launches(main) -> list[str]:
    return [
        e.get("md", "")
        for e in main.entries()
        if e["kind"] == "inbox" and " · launch · " in e.get("md", "")
    ]


async def say(world, s, text: str):
    return await world.app.registry.call(
        "session.send", {"log_id": s.log_id, "text": text}
    )


async def test_a_persons_workflow_line_hands_the_agent_a_launch_turn(world):
    main = await world.spawn()
    await say(world, main, "/workflow nap one second")
    await until(lambda: launches(main), timeout=10)
    turn_md = launches(main)[0]
    assert turn_md.startswith("> from workflow:nap · launch · ")
    assert "/workflow nap one second" in turn_md and '"secs"' in turn_md
    tok = TOKEN.search(turn_md).group(1)
    await until(lambda: main.status == "idle", timeout=10)
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "1s"}))
    )
    assert run["status"] == "running"


async def test_loop_is_the_built_in_workflow_and_shadows_the_harness(world):
    main = await world.spawn()
    await say(world, main, "/loop check the deploy every 5m")
    await until(lambda: launches(main), timeout=10)
    assert launches(main)[0].startswith("> from workflow:loop · launch · ")
    listed = await world.app.registry.call("commands.list", {"log_id": main.log_id})
    loops = [c for c in listed["commands"] if c["name"] == "loop"]
    assert [c["source"] for c in loops] == ["aegis"]
    assert {"id": "nap", "title": "Nap", "source": "project"} in listed["workflows"]


async def test_the_line_is_refused_before_any_token_when_it_cannot_run(world):
    main = await world.spawn()
    with pytest.raises(OpError) as e:
        await say(world, main, "/workflow nope later")
    assert e.value.code == "no_workflow"
    assert await say(world, main, "/workflow") is None  # the browser opens the menu
    with pytest.raises(OpError) as e:
        await say(world, main, "/loop")
    assert e.value.code == "missing_argument"
    tok = mint(world, main)
    ok(await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "30s"})))
    before = len(world.app.workflows.launches._grants)
    with pytest.raises(OpError) as e:
        await say(world, main, "/workflow nap again")
    assert e.value.code == "one_run" and "stop it from its card" in e.value.message
    assert len(world.app.workflows.launches._grants) == before


async def test_no_agent_path_mints_a_token(world):
    main, other = await world.spawn(), await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.send",
            {"log_id": main.log_id, "text": "/workflow nap 1s"},
            Caller("agent", other.log_id),
        )
    assert e.value.code == "not_for_agents"
    with pytest.raises(OpError):
        await world.app.registry.call(
            "session.send",
            {"log_id": main.log_id, "text": "/workflow nap 1s"},
            Caller("agent", main.log_id),
        )
    # An agent's handoff that reads like the line is only text in an inbox.
    said = await turn(
        other, mcp("peer_handoff", target=main.handle, context="/workflow nap 1s")
    )
    assert said.startswith("mcp ok"), said
    await until(lambda: main.status == "idle", timeout=10)
    assert world.app.workflows.launches._grants == {}
    assert launches(main) == []


async def test_loop_refuses_a_thousand_rounds_and_clamps_the_agents_wait(world):
    from aegis.workflow_lang import parse

    main = await world.spawn()
    tok = mint(world, main, "loop")
    with pytest.raises(OpError) as e:
        world.app.workflows.arm_launched(
            main, "loop", {"prompt": "x", "max": 1000}, tok
        )
    assert e.value.code == "bad_args" and "at most 100" in e.value.message
    wf, _ = parse(world.app.saved.get("loop").text, world.app.workflows._agents())
    bounds = {
        n.id: n.body["between"]
        for n in wf.by_key.values()
        if n.kind == "wait" and "between" in n.body
    }
    assert bounds == {"pause": [60.0, 3600.0], "interval": [60.0, 86400.0]}
