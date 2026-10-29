"""Pause all (#788) in the real app: the fake claude, a World, the ops."""

import asyncio
import json

import pytest

from aegis import pause
from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World

PERSON = Caller("user")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def resumed_inbox(s):
    return [
        e
        for e in s.entries()
        if e["kind"] == "inbox" and "from aegis:pause" in json.dumps(e)
    ]


async def working(s, text="/busy 30"):
    await s.send(text)
    await until(lambda: s.status == "working", what="the turn")


async def call(world, op, params=None, caller=PERSON):
    return await world.app.registry.call(op, params or {}, caller)


async def test_pause_all_ends_every_process_and_holds_new_work(world):
    busy, idle = await world.spawn(), await world.spawn()
    await working(busy)
    r = await call(world, "server.pause")
    assert r["ended"] == 2 and r["paused"]["by"]
    assert not busy.running and not idle.running
    assert busy.quota_paused == pause.WHY and idle.quota_paused is None
    assert any(
        e["kind"] == "system"
        and "paused: the person paused every session" in e["summary"]
        for e in busy.entries()
    )
    # An agent's message waits; a queue task stays pending; a spawn is refused.
    await idle.deliver("> from agent:ana · now", "please look")
    assert not idle.running and idle.held[0]["held_for"] == "quota"
    t = await asyncio.wait_for(
        world.app.queues.enqueue("solo", "hello", False, None, str(world.root)), 3
    )
    await asyncio.sleep(0.3)
    assert t.status == "pending"
    assert "paused every session" in world.app.queues.status(t)["waiting_for_quota"]
    with pytest.raises(OpError) as e:
        await call(world, "session.spawn", {"cwd": str(world.root)})
    assert e.value.code == "quota_closed"
    assert (await call(world, "server.paused"))["paused"] is not None


async def test_resume_all_continues_the_cut_turns_and_releases_the_held_work(world):
    busy, idle = await world.spawn(), await world.spawn()
    await working(busy)
    await call(world, "server.pause")
    await idle.deliver("> from agent:ana · now", "please look")
    t = await asyncio.wait_for(
        world.app.queues.enqueue("solo", "hello", False, None, str(world.root)), 3
    )
    await call(world, "server.resume")
    assert (await call(world, "server.paused"))["paused"] is None
    await until(lambda: resumed_inbox(busy), what="the resume message")
    await until(lambda: busy.status == "idle" and not busy.held, what="busy's turn")
    assert busy.quota_paused is None
    await until(lambda: not idle.held and idle.status == "idle", what="idle's message")
    assert "please look" in json.dumps(idle.entries()) and not resumed_inbox(idle)
    await until(lambda: t.status == "completed", timeout=15, what="the task")


async def test_a_persons_message_resumes_that_session_alone(world):
    a, b = await world.spawn(), await world.spawn()
    await working(a)
    await working(b)
    await call(world, "server.pause")
    await call(world, "session.send", {"log_id": a.log_id, "text": "go on"})
    await until(lambda: resumed_inbox(a), what="a's resume message")
    await until(lambda: a.status == "idle" and not a.held, what="a's turns")
    assert "go on" in json.dumps(a.entries())
    assert not b.running and b.quota_paused == pause.WHY
    # An agent cannot resume one: its message waits.
    await b.deliver("> from agent:a · now", "wake up")
    assert not b.running and b.held


async def test_a_pause_lasts_across_a_restart_and_holds_the_cut_turns(world):
    s, other = await world.spawn(), await world.spawn()
    await working(s)
    await call(world, "server.pause")
    log_id, other_id = s.log_id, other.log_id
    await world.restart()
    s, other = world.session(log_id), world.session(other_id)
    await asyncio.sleep(0.3)
    assert world.app.paused is not None
    assert not s.running and s.quota_paused == pause.WHY
    await other.deliver("> from agent:ana · now", "after the restart")
    assert not other.running and other.held
    await call(world, "server.resume")
    await until(lambda: resumed_inbox(s), what="the resume after the restart")
    await until(lambda: s.status == "idle" and not s.held, what="its turn")


async def test_serve_paused_holds_what_a_restart_would_continue(world):
    s = await world.spawn()
    await working(s)
    log_id = s.log_id
    # What aegis serve --paused writes before the server boots.
    pause.write(world.app.roots.state_root, by="aegis serve --paused")
    await world.restart()
    s = world.session(log_id)
    await asyncio.sleep(0.5)
    assert not s.running and s.held, "the restart's continue waits"
    await call(world, "server.resume")
    await until(
        lambda: s.running and s.status == "idle" and not s.held, what="the continue"
    )
