"""Parked sessions on the real app and the fake claude: the state by hand here,
the loop that parks them further down (task 5)."""

import asyncio
import os
import signal

import pytest

from aegis import park

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn


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
    assert a.asleep_why == "idle" and not a.running and a.parked_rss > 0
    assert a.entries()[-1]["summary"] == "asleep after 20 min idle"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    reply = await turn(a, "/recall")
    assert "hello ana" in reply  # the same harness session, resumed
    assert a.status == "idle" and a.running and a.parked_rss == 0


async def test_a_turn_or_a_held_message_wins_over_a_park(world):
    a = await world.spawn()
    await turn(a, "/hello ana")
    sending = asyncio.create_task(a.send("/busy 1"))
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
    assert not (a.asleep_why == "idle" and a.running)
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
    assert a.status == "asleep"
    assert a.entries()[-1]["summary"] == "asleep"


async def test_a_parked_session_stays_parked_across_a_restart_and_is_not_continued(
    world,
):
    a = await world.spawn()
    await turn(a, "hello ana")
    await a.park(1200)
    log_id = a.log_id
    await world.restart()
    b = world.session(log_id)
    assert b.asleep_why == "idle" and not b.running
    await asyncio.sleep(0.5)
    assert b.asleep_why == "idle"  # nothing continued it
    assert "hello ana" in await turn(b, "/recall")


async def test_a_parked_session_is_listed_as_parked_and_still_nudged(
    world, monkeypatch
):
    # Off until it is parked: the turn's end would nudge it before the park.
    monkeypatch.setattr(world.app.nudges, "timings", lambda: (None, 900.0))
    a = await world.spawn()
    # A silent end: a turn_end would say how it ended, and nothing nudges that.
    await turn(a, "write the summary")
    await a.park(1200)
    monkeypatch.setattr(world.app.nudges, "timings", lambda: (0.0, 900.0))
    rows = await world.app.registry.call("session.list", {})
    assert next(r for r in rows if r["handle"] == a.handle)["state"] == "asleep"
    await world.app.nudges.tick()
    await until(lambda: a.running, timeout=5, what="the nudge waking it")


# -- the loop ----------------------------------------------------------------------
@pytest.fixture
def fast(monkeypatch):
    """Before `parking`: the loop reads EVERY_S as it goes to sleep."""
    monkeypatch.setattr(park, "EVERY_S", 0.05)


@pytest.fixture
async def parking(tmp_path, fake_claude, fast):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "park: {claude-code: 1}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def done(s):
    await turn(s, mcp("turn_end", attention="done", line="finished"))


async def leave_a_sleep(s, seconds: str, pidfile) -> int:
    """A `nohup sleep &` from a Bash call: its shell exits, so it leaves the
    harness's tree. Returns its pid, to kill by pid when the test ends."""
    await turn(s, f"/sh nohup sleep {seconds} >/dev/null 2>&1 & echo $! > {pidfile}")
    return int(pidfile.read_text())


def kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def test_an_idle_session_that_said_done_parks(parking):
    a = await parking.spawn()
    await done(a)
    await until(lambda: a.asleep_why == "idle", timeout=5, what="the park")


async def test_a_turn_that_did_not_end_with_turn_end_does_not_park(parking):
    a = await parking.spawn()
    await turn(a, "/hello ana")
    await asyncio.sleep(1.5)
    assert a.status == "idle" and a.running


async def test_a_background_task_keeps_it_up(parking):
    a = await parking.spawn()
    await a.send("/bgtask 2")
    await until(lambda: a.open_tasks, what="the background task")
    await asyncio.sleep(1.5)
    assert a.running


async def test_a_process_it_left_behind_keeps_it_up(parking, tmp_path):
    a = await parking.spawn()
    pid = await leave_a_sleep(a, "20.37", tmp_path / "pid")
    try:
        await done(a)
        await asyncio.sleep(1.5)
        assert a.running
    finally:
        kill(pid)


async def test_the_left_behind_test_can_fail(parking, monkeypatch, tmp_path):
    monkeypatch.setattr(park, "strays", lambda *a, **k: [])
    a = await parking.spawn()
    pid = await leave_a_sleep(a, "21.37", tmp_path / "pid")
    try:
        await done(a)
        await until(lambda: a.asleep_why == "idle", timeout=5, what="the park")
    finally:
        kill(pid)


async def test_a_monitor_wakes_a_parked_session(parking, tmp_path):
    a = await parking.spawn()
    flag = tmp_path / "flag"
    await turn(
        a,
        mcp(
            "monitor_start",
            description="the flag",
            done=f"test -e {flag}",
            progress=None,
            interval_s=1,
            source=None,
        ),
    )
    await until(
        lambda: a.asleep_why == "idle", timeout=5, what="the park while it waits"
    )
    flag.touch()
    await until(
        lambda: any(e["title"].startswith("monitor:") for e in inbox(a)),
        timeout=8,
        what="the monitor's wake",
    )


# Slow: a park, a config edit and a 1.3 s wait run near the 3 s fast-lane limit.
@pytest.mark.slow
async def test_removing_the_harness_from_park_stops_new_parks_and_keeps_parked_ones(
    parking,
):
    # b has not ended a turn yet, so it cannot park before the edit.
    a, b = await parking.spawn(), await parking.spawn()
    await done(a)
    await until(lambda: a.asleep_why == "idle", timeout=5, what="the park")
    (parking.root / ".aegis.yaml").write_text(CONFIG)
    await done(b)
    await asyncio.sleep(1.3)  # b would have parked after 1 s
    assert b.running and a.asleep_why == "idle"


async def test_a_wrong_park_block_parks_nothing(tmp_path, fake_claude, fast):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "park: {claude-code: 0}\n")
    w = await World(tmp_path, fake_claude).start()
    try:
        a = await w.spawn()
        await done(a)
        await asyncio.sleep(1.5)
        assert a.running
    finally:
        await w.stop()
