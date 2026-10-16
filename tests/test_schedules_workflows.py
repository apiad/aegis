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
