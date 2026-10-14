"""A schedule's run as a session: its schedule meta, what spawn: false
refuses, and the fake claude reading a scheduled prompt."""

import pytest

from .test_agents import CONFIG, World, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


RUN = {"name": "pr-triage", "run": 3, "spawn": False}


async def test_a_run_with_spawn_off_is_refused_spawn_and_enqueue(world):
    a = await world.spawn()
    a.schedule = RUN
    for call in (
        mcp("session_spawn", agent="opus", prompt="hi"),
        mcp("queue_enqueue", queue="general", payload="hi"),
    ):
        said = await turn(a, call)
        assert "run 3 of pr-triage may not start other work (spawn: false)" in said
    assert len(world.app.sessions.sessions) == 1 and not world.app.queues.tasks


async def test_with_spawn_on_a_child_carries_the_schedule(world):
    a = await world.spawn()
    a.schedule = RUN | {"spawn": True}
    said = await turn(a, mcp("session_spawn", agent="opus", prompt="hi"))
    assert said.startswith("mcp ok")
    (kid,) = [
        o for o in world.app.sessions.sessions.values() if o.spec.spawned_by == a.log_id
    ]
    assert kid.schedule == RUN | {"spawn": True}
    assert kid.meta()["schedule"] == RUN | {"spawn": True}


async def test_the_schedule_meta_survives_a_restart(world):
    a = await world.spawn()
    a.schedule = RUN
    world.app.sessions.metas.write(a.meta())
    await world.restart()
    assert world.session(a.log_id).schedule == RUN


async def test_the_fake_runs_the_line_under_a_schedule_header(world):
    a = await world.spawn()
    head = "> from schedule:x-y · run#1 · tick 09:00"
    done = mcp("turn_end", attention="done", line="ok")
    await turn(a, f"{head}\n{done}\n\n```text\nstdout\n```")
    assert a.standing["report"]["attention"] == "done"
    assert inbox(a)[0]["title"] == "schedule:x-y · run#1 · tick 09:00"
