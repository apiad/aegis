"""Parked sessions on the real app and the fake claude: the state by hand here,
the loop that parks them further down (task 5)."""

import asyncio
import os

import pytest

from aegis import nudges

from .conftest import until
from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_a_parked_session_has_no_process_and_resumes_with_its_context(world):
    a = await world.spawn()
    # A plain prompt: the fake's /hello answers before /recall can list it.
    await turn(a, "hello ana")
    pid = a.pid
    assert await a.park(1200)
    assert a.status == "parked" and not a.running and a.parked_rss > 0
    assert a.entries()[-1]["summary"] == "parked after 20 min idle"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    reply = await turn(a, "/recall")
    assert "hello ana" in reply  # the same harness session, resumed
    assert a.status == "idle" and a.running and a.parked_rss == 0


async def test_a_turn_or_a_held_message_wins_over_a_park(world):
    a = await world.spawn()
    await turn(a, "/hello ana")
    sending = asyncio.create_task(a.send("/sleep 1"))
    await until(lambda: a.status == "working", what="the turn")
    assert not await a.park(1200)
    await sending
    await until(lambda: a.status == "idle", timeout=5, what="the end of the turn")


async def test_a_message_racing_a_park_is_delivered(world):
    a = await world.spawn()
    await turn(a, "/hello ana")
    # The old process ends slower than a new one starts: without the lock the
    # message's process would be running when the park releases the session's
    # cgroup, which kills what is in it.
    proc, term = a._proc, a._proc.terminate

    async def slow_terminate():
        await asyncio.sleep(0.5)
        await term()

    proc.terminate = slow_terminate
    parking = asyncio.create_task(a.park(1200))
    await asyncio.sleep(0)  # the park is inside _end_process
    await a.deliver("> from agent:ben · now", "/hello again")
    assert await parking
    assert not (a.status == "parked" and a.running)
    await until(
        lambda: any("hello again" in (e.get("md") or "") for e in a.entries()),
        timeout=8,
        what="the delivered message",
    )


async def test_stop_on_a_parked_session_leaves_it_stopped(world):
    a = await world.spawn()
    await turn(a, "/hello ana")
    await a.park(1200)
    await a.stop()
    assert a.status == "stopped"
    assert a.entries()[-1]["summary"] == "stopped"


async def test_a_parked_session_stays_parked_across_a_restart_and_is_not_continued(
    world,
):
    a = await world.spawn()
    await turn(a, "hello ana")
    await a.park(1200)
    log_id = a.log_id
    await world.restart()
    b = world.session(log_id)
    assert b.status == "parked" and not b.running
    await asyncio.sleep(0.5)
    assert b.status == "parked"  # nothing continued it
    assert "hello ana" in await turn(b, "/recall")


async def test_a_parked_session_is_listed_as_parked_and_still_nudged(
    world, monkeypatch
):
    monkeypatch.setattr(nudges, "IDLE_S", 0.0)
    a = await world.spawn()
    # A silent end: a turn_end would say how it ended, and nothing nudges that.
    await turn(a, "write the summary")
    await a.park(1200)
    rows = await world.app.registry.call("session.list", {})
    assert next(r for r in rows if r["handle"] == a.handle)["state"] == "parked"
    await world.app.nudges.tick()
    await until(lambda: a.running, timeout=5, what="the nudge waking it")
