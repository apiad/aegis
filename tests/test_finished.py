"""Registry.finished, the one rule a queue's task and a schedule's run end on,
and run_bash, the one way a monitor and a schedule run a command."""

import pytest

from aegis.monitors import run_bash

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_a_session_with_a_working_child_is_not_finished(world):
    reg = world.app.sessions
    a = await world.spawn()
    await turn(a, mcp("session_spawn", agent="opus", prompt="/busy 1"))
    (kid,) = [o for o in reg.sessions.values() if o.spec.spawned_by == a.log_id]
    assert kid.status == "working" and a.status == "idle"
    assert not reg.finished(a)
    await until(lambda: reg.finished(a), 10, "the child's turn")


async def test_run_bash_says_the_code_the_output_and_why_it_could_not_run():
    assert await run_bash("echo hi; exit 3", "/", user=None) == (3, "hi\n", "")
    assert await run_bash("sleep 5", "/", timeout=0.2, user=None) == (
        None,
        "",
        "took over 0.2s",
    )
    rc, _, err = await run_bash("true", "/no/such/dir", user=None)
    assert rc is None and "No such file" in err
