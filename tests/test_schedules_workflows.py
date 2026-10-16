"""Schedules that fire saved workflows (spec §06, §09): the schedule's
approval covers its runs, and it pins the version a person approved."""

import pytest

from aegis.ops import OpError
from aegis.schedule_log import Definition

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_schedules_e2e import frozen, runs, tick
from .test_workflow_store import BY, NAP
from .test_workflows_e2e import ok
from .test_workflows_launch import TOKEN


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.saved.save(NAP, w.app.workflows._agents(), BY)
    yield w
    await w.stop()


def person_schedule(world, name="nightly-nap", prompt="/workflow nap one second"):
    raw = {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": prompt}
    return world.app.schedules.create(
        name,
        Definition.model_validate(raw),
        {"kind": "user", "principal": world.app.user},
        world.root,
    )


async def save(world, text: str):
    """A save the way a person or an agent makes one, which tells the schedules."""
    return await world.app.registry.call("workflow.save", {"definition": text})


def launches(ses) -> list[str]:
    """The launch turns a schedule's fires sent, under the schedule's header."""
    return [
        e["md"]
        for e in ses.entries()
        if e["kind"] == "inbox"
        and e["title"].startswith("schedule:")
        and "launches the saved workflow" in e["md"]
    ]


def fired(world, name):
    (rec,) = runs(world, name, "fired")
    return world.app.sessions.sessions[rec["log_id"]]


async def test_a_persons_schedule_fires_its_workflow_with_no_click(world):
    # [[slow]] holds the fake's turn on the launch for 1.5 s: a real agent
    # arms inside that turn, so the schedule's run never looks finished.
    s = person_schedule(world, prompt="/workflow nap one second [[slow]]")
    assert s.pin == {"id": "nap", "sha": world.app.saved.get("nap").sha}
    t = frozen(world)
    await tick(world, t)
    ses = fired(world, "nightly-nap")
    await until(lambda: launches(ses), timeout=10)
    md = launches(ses)[0]
    assert "Schedule nightly-nap typed: /workflow nap one second" in md
    assert ses.status == "working"
    tok = TOKEN.search(md).group(1)
    st = world.app.workflows.arm_launched(ses, "nap", {"secs": "2s"}, tok)
    assert st.status == "running" and st.launch["by"] == "schedule"
    # The session goes idle while the wait runs; the schedule's run stays live.
    await until(lambda: ses.status == "idle", timeout=10)
    await world.app.schedules.step()
    assert world.app.schedules.state["nightly-nap"].live
    await until(lambda: st.status == "done", timeout=10)
    await until(lambda: not world.app.schedules.state["nightly-nap"].live, timeout=10)


async def test_an_agents_workflow_schedule_waits_and_unpause_pins_what_the_card_showed(
    world,
):
    main = await world.spawn()
    ok(
        await turn(
            main,
            mcp(
                "schedule_create",
                name="agent-nap",
                clock={"every": "5m"},
                fire={"agent": "opus"},
                prompt="/workflow nap a second",
            ),
        )
    )
    sch = world.app.schedules
    assert sch.awaiting("agent-nap")
    t = frozen(world)
    await tick(world, t)
    assert runs(world, "agent-nap", "fired") == []  # nothing runs before a person
    said = await turn(main, mcp("schedule_resume", name="agent-nap"))
    assert said.startswith("mcp error") and "approval" in said  # slice 0's refusal
    sha = world.app.saved.get("nap").sha
    with pytest.raises(OpError, match="the version"):
        await world.app.registry.call("schedule.resume", {"name": "agent-nap"})
    # The agent changes the workflow while the person reads the card.
    await save(world, NAP.replace("Nap a while.", "Nap longer."))
    with pytest.raises(OpError, match="changed while"):
        await world.app.registry.call(
            "schedule.resume", {"name": "agent-nap", "workflow": sha}
        )
    new = world.app.saved.get("nap").sha
    await world.app.registry.call(
        "schedule.resume", {"name": "agent-nap", "workflow": new}
    )
    assert sch.items["agent-nap"].pin["sha"] == new
    assert not sch.items["agent-nap"].paused


async def test_a_changed_workflow_pauses_the_schedule_with_its_diff(world):
    person_schedule(world)
    await save(world, NAP.replace("Nap a while.", "Nap longer."))
    sch = world.app.schedules
    assert sch.awaiting("nightly-nap")  # the save paused it at once
    w = sch.wire("nightly-nap")["workflow"]
    assert w["changed"] and w["pinned"] != w["sha"]
    assert w["steps"] == ["Wait {{args.secs}}."]
    assert w["diff"]
    # A hand edit, which no save sees, is caught at the fire.
    sch.resume("nightly-nap", workflow=w["sha"])
    p = world.app.saved.get("nap").path
    p.write_text(p.read_text().replace("Nap longer.", "Nap by hand."))
    t = frozen(world)
    await tick(world, t)
    assert runs(world, "nightly-nap", "fired") == []
    assert sch.awaiting("nightly-nap")


async def test_a_change_asks_again_even_when_the_person_had_paused_it(world):
    person_schedule(world)
    sch = world.app.schedules
    sch.pause("nightly-nap")
    await save(world, NAP.replace("Nap a while.", "Nap longer."))
    assert sch.awaiting("nightly-nap")
    with pytest.raises(OpError, match="the version"):
        sch.resume("nightly-nap")


async def test_a_schedule_names_a_workflow_that_exists(world):
    with pytest.raises(OpError) as e:
        person_schedule(world, prompt="/workflow nope now")
    assert e.value.code == "no_workflow"


async def test_an_existing_target_with_a_run_holds_the_tick(world):
    main = await world.spawn()
    tok = world.app.workflows.launches.issue(
        main.log_id,
        world.app.saved.get("nap"),
        "x",
        {"by": "person", "principal": "ana", "line": "/workflow nap x"},
    )
    run = ok(
        await turn(main, mcp("workflow_arm", id="nap", token=tok, args={"secs": "2s"}))
    )
    raw = {
        "clock": {"every": "5m"},
        "fire": {"target": "existing", "session": main.log_id},
        "prompt": "/workflow nap again",
    }
    world.app.schedules.create(
        "into-main",
        Definition.model_validate(raw),
        {"kind": "user", "principal": world.app.user},
        world.root,
    )
    t = frozen(world)
    await tick(world, t)
    (held,) = [r for r in runs(world, "into-main", "tick") if r["out"] == "held"]
    assert held["why"] == "workflow"
    await until(
        lambda: world.app.workflows.runs[run["run"]].status == "done", timeout=10
    )
    await world.app.schedules.step()
    await until(lambda: runs(world, "into-main", "fired"), timeout=10)


async def test_a_save_during_the_spawn_does_not_change_what_the_fire_launches(world):
    # The fire compares the pin, then awaits the spawn: a save landing in that
    # await must not swap the launched text for one nobody approved.
    s = person_schedule(world, prompt="/workflow nap one second [[slow]]")
    pinned = s.pin["sha"]
    reg = world.app.schedules._reg
    orig = reg.spawn

    async def spawn(*a, **k):
        ses = await orig(*a, **k)
        evil = NAP.replace("Nap a while.", "Not what was approved.")
        world.app.saved.save(evil, world.app.workflows._agents(), {"kind": "agent"})
        return ses

    reg.spawn = spawn
    t = frozen(world)
    await tick(world, t)
    ses = fired(world, "nightly-nap")
    await until(lambda: launches(ses), timeout=10)
    tok = TOKEN.search(launches(ses)[0]).group(1)
    st = world.app.workflows.arm_launched(ses, "nap", {"secs": "1s"}, tok)
    assert st.saved["sha"] == pinned
    assert "Not what was approved" not in st.text


async def test_a_schedule_launch_refuses_a_version_other_than_its_pin(world):
    s = person_schedule(world)
    main = await world.spawn()
    world.app.saved.save(
        NAP.replace("Nap a while.", "Nap longer."), world.app.workflows._agents(), BY
    )
    other = world.app.saved.get("nap")
    assert other.sha != s.pin["sha"]
    origin = {"by": "schedule", "name": "nightly-nap", "run": 1, "line": "x"}
    with pytest.raises(OpError) as e:
        world.app.workflows.launch(main, other, "x", origin)
    assert e.value.code == "changed"
    assert not world.app.workflows.launches._grants


async def agent_nap(world, main):
    ok(
        await turn(
            main,
            mcp(
                "schedule_create",
                name="agent-nap",
                clock={"every": "1d"},
                fire={"agent": "opus"},
                prompt="/workflow nap a second",
            ),
        )
    )
    sha = world.app.saved.get("nap").sha
    await world.app.registry.call(
        "schedule.resume", {"name": "agent-nap", "workflow": sha}
    )


@pytest.mark.parametrize("paused", [False, True])
async def test_an_agent_cannot_fire_a_workflow_schedule(world, paused):
    # The person approved a clock, not on-demand runs: no agent fires it,
    # paused or not.
    main = await world.spawn()
    await agent_nap(world, main)
    if paused:
        await world.app.registry.call("schedule.pause", {"name": "agent-nap"})
    said = await turn(main, mcp("schedule_fire", name="agent-nap"))
    assert said.startswith("mcp error") and "workflow" in said
    assert runs(world, "agent-nap", "fired") == []
    assert not world.app.workflows.launches._grants


async def test_a_person_fires_a_paused_workflow_schedule_only_when_approved(world):
    sch = world.app.schedules
    person_schedule(world)
    sch.pause("nightly-nap")
    # Paused by the person, and the pin is the current version: it fires.
    assert await sch.fire_now("nightly-nap") == "fired"
    # A save asks for approval again: fire-now is refused, and nothing fires.
    await save(world, NAP.replace("Nap a while.", "Nap longer."))
    assert sch.awaiting("nightly-nap")
    with pytest.raises(OpError) as e:
        await sch.fire_now("nightly-nap")
    assert e.value.code == "needs_approval"
    assert len(runs(world, "nightly-nap", "fired")) == 1


async def test_a_person_cannot_fire_a_paused_schedule_whose_file_changed_by_hand(
    world,
):
    sch = world.app.schedules
    person_schedule(world)
    sch.pause("nightly-nap")
    p = world.app.saved.get("nap").path
    p.write_text(p.read_text().replace("Nap a while.", "Nap by hand."))
    with pytest.raises(OpError) as e:
        await sch.fire_now("nightly-nap")
    assert e.value.code == "needs_approval"
    assert runs(world, "nightly-nap", "fired") == []


def edit(world, name, prompt, every="10m"):
    raw = {"clock": {"every": every}, "fire": {"agent": "opus"}, "prompt": prompt}
    return world.app.schedules.update(name, Definition.model_validate(raw), world.root)


async def test_a_persons_unrelated_edit_keeps_the_pin(world):
    # A version the person never approved must not ride in on a clock edit.
    sch = world.app.schedules
    approved = person_schedule(world).pin["sha"]
    p = world.app.saved.get("nap").path
    p.write_text(p.read_text().replace("Nap a while.", "Nap by hand."))
    edit(world, "nightly-nap", "/workflow nap two seconds")
    assert sch.items["nightly-nap"].pin["sha"] == approved
    t = frozen(world)
    await tick(world, t, 600)
    assert runs(world, "nightly-nap", "fired") == []
    assert sch.awaiting("nightly-nap")


async def test_a_persons_edit_naming_another_workflow_waits_for_approval(world):
    sch = world.app.schedules
    person_schedule(world)
    world.app.saved.save(
        NAP.replace("id: nap", "id: doze"), world.app.workflows._agents(), BY
    )
    edit(world, "nightly-nap", "/workflow doze a while")
    s = sch.items["nightly-nap"]
    assert s.pin == {"id": "doze", "sha": world.app.saved.get("doze").sha}
    assert sch.awaiting("nightly-nap")
    edit(world, "nightly-nap", "Just say hi.")
    assert sch.items["nightly-nap"].pin is None


async def test_a_loop_schedule_from_before_pins_can_be_approved_and_fires(world):
    # A /loop schedule saved before schedules pinned workflows has no pin:
    # its fire waits for a person, whose unpause pins the version shown.
    sch = world.app.schedules
    s = person_schedule(world, name="old-loop", prompt="/loop 5m check the build")
    s.pin = None
    t = frozen(world)
    await tick(world, t)
    assert runs(world, "old-loop", "fired") == [] and sch.awaiting("old-loop")
    w = sch.wire("old-loop")["workflow"]
    sha = world.app.saved.get("loop").sha
    assert w["id"] == "loop" and w["sha"] == sha and w["changed"]
    with pytest.raises(OpError) as e:
        sch.resume("old-loop")
    assert e.value.code == "needs_version"
    sch.resume("old-loop", workflow=sha)
    assert sch.items["old-loop"].pin == {"id": "loop", "sha": sha}
    await tick(world, t)
    assert runs(world, "old-loop", "fired")


SHELL = """id: tidy
title: Tidy
say: Tidy the tree.
root:
  seq:
    - {sh: "git clean -n", id: look, say: Look at what is untracked.}
    - {check: {sh: "test -f done", every: 5s}, id: wait-done, say: Wait for done.}
  say: Tidy.
"""


async def test_a_schedules_card_lists_the_commands_its_workflow_runs(world):
    # The person approves the commands as on an arm card: none is hidden.
    world.app.saved.save(SHELL, world.app.workflows._agents(), BY)
    person_schedule(world, name="tidy-up", prompt="/workflow tidy now")
    w = world.app.schedules.wire("tidy-up")["workflow"]
    assert w["commands"]["sh"] == 1 and w["commands"]["probes"] == 1
    assert w["cmds"] == ["$ git clean -n", "$ test -f done"]
    assert w["cwd"] == str(world.root)


async def test_an_unapproved_arm_does_not_keep_a_schedules_run_live(world):
    # Only a running workflow is work in progress: one armed and waiting for
    # a click nobody may give must not hold the schedule's overlap forever.
    raw = {
        "clock": {"every": "5m"},
        "fire": {"agent": "opus"},
        "prompt": "hello [[slow]]",
    }
    world.app.schedules.create(
        "say-hello",
        Definition.model_validate(raw),
        {"kind": "user", "principal": world.app.user},
        world.root,
    )
    t = frozen(world)
    await tick(world, t)
    ses = fired(world, "say-hello")
    await until(lambda: ses.status == "working", timeout=10)
    st = world.app.workflows.arm(ses, NAP, {"secs": "1s"})
    assert st.status == "armed"
    await until(lambda: ses.status == "idle", timeout=10)
    await world.app.schedules.step()
    await until(lambda: not world.app.schedules.state["say-hello"].live, timeout=10)


async def test_the_pinned_text_outlives_later_saves(world):
    person_schedule(world)
    await save(world, NAP.replace("Nap a while.", "Nap two."))
    await save(world, NAP.replace("Nap a while.", "Nap three."))
    w = world.app.schedules.wire("nightly-nap")["workflow"]
    assert w["changed"] and w["diff"]  # drawn from the pinned version's text


async def unpinned_agent_nap(world):
    """An agent's workflow schedule as a server from before pins saved it:
    approved and running, with no pin."""
    main = await world.spawn()
    await agent_nap(world, main)
    s = world.app.schedules.items["agent-nap"]
    s.pin, s.paused = None, None
    world.app.schedules._save()
    return main


async def agent_pins_new_text(world, main) -> str:
    """The reviewer's scenario: the agent saves new text and resumes the
    schedule with its sha, then the clock comes round."""
    await save(world, NAP.replace("Nap a while.", "Not what was approved."))
    sha = world.app.saved.get("nap").sha
    said = await turn(main, mcp("schedule_resume", name="agent-nap", workflow=sha))
    await tick(world, frozen(world), 86400)
    return said


async def test_a_schedule_loaded_without_a_pin_waits_for_a_person(world):
    main = await unpinned_agent_nap(world)
    await world.restart()
    main = world.app.sessions.sessions[main.log_id]
    assert world.app.schedules.awaiting("agent-nap")
    said = await agent_pins_new_text(world, main)
    assert said.startswith("mcp error")
    assert world.app.schedules.items["agent-nap"].pin is None
    assert runs(world, "agent-nap", "fired") == []


async def test_an_agent_cannot_pin_a_schedule_that_has_no_pin(world):
    main = await unpinned_agent_nap(world)
    said = await agent_pins_new_text(world, main)
    assert said.startswith("mcp error") and "person" in said
    assert world.app.schedules.items["agent-nap"].pin is None
    assert runs(world, "agent-nap", "fired") == []


def spy_schedules(world, monkeypatch) -> list[dict]:
    """The schedule cards the server publishes, as a browser receives them."""
    got: list[dict] = []
    publish = world.app.publish

    def spy(channel, ops):
        if channel == "schedules":
            got.extend(op["upsert"] for op in ops if "upsert" in op)
        return publish(channel, ops)

    monkeypatch.setattr(world.app, "publish", spy)
    return got


async def test_every_save_redraws_a_schedule_already_waiting(world, monkeypatch):
    # A second save while approval is pending pauses nothing new, but the
    # card must show the version an Unpause now approves.
    person_schedule(world)
    await save(world, NAP.replace("Nap a while.", "Nap two."))
    got = spy_schedules(world, monkeypatch)
    await save(world, NAP.replace("Nap a while.", "Nap three."))
    sha = world.app.saved.get("nap").sha
    assert [c["workflow"]["sha"] for c in got if c["name"] == "nightly-nap"][-1:] == [
        sha
    ]
    # A refused Unpause redraws the card with the version it would approve.
    got.clear()
    with pytest.raises(OpError, match="changed while"):
        await world.app.registry.call(
            "schedule.resume", {"name": "nightly-nap", "workflow": "stale"}
        )
    assert [c["workflow"]["sha"] for c in got if c["name"] == "nightly-nap"] == [sha]
