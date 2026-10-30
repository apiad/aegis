"""An agent hands off its own session with session_handoff (#874)."""

import os
import signal

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def successor(world, a):
    return next(
        (
            s
            for s in world.app.sessions.sessions.values()
            if s.spec.continues == a.log_id
        ),
        None,
    )


async def ask(world, a, **params):
    return await world.app.registry.call(
        "session.handoff", params, Caller("agent", a.log_id)
    )


async def working(a):
    await a.send("[[slow]] busy")
    await until(lambda: a.in_turn, 8, "the turn started")


def first_message(s) -> str:
    return next(str(e) for e in s.entries() if e["kind"] == "user")


async def test_the_new_session_starts_when_the_turn_ends(world):
    a = await world.spawn()
    await turn(a, mcp("session_handoff", msg="implement the plan"))
    await until(lambda: successor(world, a) is not None, 8, "the new session")
    b = successor(world, a)
    await until(lambda: any(e["kind"] == "user" for e in b.entries()), 8, "its prompt")
    assert "implement the plan" in first_message(b)
    assert "session_handoff" in first_message(b)  # the turn that asked is in the tail
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")


async def test_the_lock_starts_at_the_call(world):
    a = await world.spawn()
    await working(a)
    said = await ask(world, a, msg="go on")
    assert "End your turn" in said and a.continued_by == "pending"
    assert successor(world, a) is None
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.send", {"log_id": a.log_id, "text": "hey"}, Caller("user")
        )
    assert e.value.code == "handed_off"
    await until(lambda: successor(world, a) is not None, 8, "the new session")


async def test_a_second_call_is_refused(world):
    a = await world.spawn()
    await working(a)
    await ask(world, a)
    with pytest.raises(OpError) as e:
        await ask(world, a)
    assert e.value.code == "handed_off"


async def test_an_agent_cannot_hand_off_another_session(world):
    a, c = await world.spawn(), await world.spawn()
    with pytest.raises(OpError) as e:
        await ask(world, a, log_id=c.log_id)
    assert e.value.code == "not_yours" and c.continued_by is None


async def test_called_outside_a_turn_it_starts_now(world):
    a = await world.spawn()
    r = await ask(world, a, msg="go on")
    assert a.continued_by == r["log_id"]


async def test_held_messages_refuse_the_call(world):
    a = await world.spawn()
    await working(a)
    c = await world.spawn()
    await world.app.registry.call(
        "peer.handoff",
        {"target": a.handle, "context": "psst"},
        Caller("agent", c.log_id),
    )
    with pytest.raises(OpError) as e:
        await ask(world, a)
    assert e.value.code == "held_messages" and a.continued_by is None


async def test_a_monitor_refuses_the_call(world):
    a = await world.spawn()
    params = {"description": "w", "done": "false", "progress": None, "source": None}
    await world.app.registry.call("monitor.start", params, Caller("agent", a.log_id))
    with pytest.raises(OpError) as e:
        await ask(world, a)
    assert e.value.code == "has_wakers"


async def test_a_failed_turn_clears_the_request_and_the_lock(world, monkeypatch):
    a = await world.spawn()
    await working(a)
    await ask(world, a)
    reg = world.app.sessions
    real = reg._pending_handoff

    def failed(s):
        s.standing["turn_error"] = "the model refused"  # what a failed Result leaves
        real(s)

    monkeypatch.setattr(reg, "_pending_handoff", failed)
    await until(lambda: a.continued_by is None, 8, "the handoff called off")
    assert a.handoff_pending is None and successor(world, a) is None


async def test_a_dead_process_clears_the_request_and_the_lock(world):
    a = await world.spawn()
    await working(a)
    await ask(world, a)
    os.kill(a.wire()["pid"], signal.SIGKILL)
    await until(lambda: a.continued_by is None, 8, "the handoff called off")
    assert successor(world, a) is None


async def test_interrupt_ends_the_turn_and_the_new_session_starts(world):
    a = await world.spawn()
    await working(a)
    await ask(world, a)
    await a.interrupt()
    await until(lambda: successor(world, a) is not None, 8, "the new session")


async def test_a_restart_with_pending_starts_it(world):
    a = await world.spawn()
    await working(a)
    await ask(world, a, msg="go on")
    log_id = a.log_id
    await world.restart()
    reg = world.app.sessions
    await until(lambda: log_id in reg.archived, 15, "the old session retired")


async def test_permission_is_capped_at_the_callers(world):
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus", "permission": "write"}
    )
    a = world.session(r["log_id"])
    with pytest.raises(OpError) as e:
        await ask(world, a, agent="opus")  # its preset is full
    assert e.value.code == "not_allowed" and a.continued_by is None
    await ask(world, a, agent="opus", permission="write")
    assert a.continued_by is not None


async def test_an_unknown_agent_is_refused_at_the_call(world):
    a = await world.spawn()
    await working(a)
    with pytest.raises(OpError) as e:
        await ask(world, a, agent="nobody")
    assert e.value.code == "unknown_agent" and a.continued_by is None


async def test_spawn_false_on_a_schedule_run_refuses(world):
    a = await world.spawn()
    a.schedule = {"run": 1, "name": "nightly", "spawn": False}
    with pytest.raises(OpError) as e:
        await ask(world, a)
    assert e.value.code == "spawn_off"
