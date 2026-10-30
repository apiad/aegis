"""A handed-off session refuses messages until its successor retires it (#874)."""

import os
import signal

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, turn
from .test_context_e2e import handed, handoff


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def send(world, s, text):
    params = {"log_id": s.log_id, "text": text}
    return await world.app.registry.call("session.send", params, Caller("user"))


def called_off(s) -> str:
    return " ".join(str(e.get("summary")) for e in s.entries() if e["kind"] == "system")


async def test_a_person_cannot_write_to_a_handed_off_session(world):
    a, b = await handed(world, "[[slow]] Brief.")
    with pytest.raises(OpError) as e:
        await send(world, a, "still here")
    assert e.value.code == "handed_off" and b.handle in e.value.message


async def test_a_slash_line_is_refused_too(world):
    a, b = await handed(world, "[[slow]] Brief.")
    with pytest.raises(OpError) as e:
        await send(world, a, "/sleep")
    assert e.value.code == "handed_off"


async def test_a_peer_handoff_to_it_is_refused(world):
    a, b = await handed(world, "[[slow]] Brief.")
    c = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "peer.handoff",
            {"target": a.handle, "context": "psst"},
            Caller("agent", c.log_id),
        )
    assert e.value.code == "handed_off"


async def test_the_lock_outlasts_a_message_and_the_archive_still_happens(world):
    a, b = await handed(world, "[[slow]] Brief.")
    with pytest.raises(OpError):
        await send(world, a, "still here")
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")


async def test_archive_still_works_on_it(world):
    a, b = await handed(world, "[[slow]] Brief.")
    reg = world.app.sessions
    await reg.close(a.log_id)
    assert a.log_id in reg.archived


async def test_it_cannot_be_reopened_once_retired(world):
    a, b = await handed(world)
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "archived")
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.reopen", {"log_id": a.log_id}, Caller("user")
        )
    assert e.value.code == "handed_off" and b.handle in e.value.message
    assert a.log_id in reg.archived  # the refusal took nothing out of the archive


async def test_a_failed_first_turn_unlocks_and_notes_it(world):
    a, b = await handed(world, "[[slow]] Brief.")
    await b.interrupt()
    await until(lambda: a.continued_by is None, 8, "the handoff called off")
    assert a.log_id in world.app.sessions.sessions
    assert "handoff called off" in called_off(a)
    await send(world, a, "back to work")  # not refused


async def test_a_dead_successor_unlocks(world):
    a, b = await handed(world, "[[slow]] Brief.")
    os.kill(b.wire()["pid"], signal.SIGKILL)
    await until(lambda: a.continued_by is None, 8, "the handoff called off")
    assert "handoff called off" in called_off(a)
    await send(world, a, "back to work")


async def test_closing_the_successor_unlocks_and_it_can_be_reopened_later(world):
    a, b = await handed(world, "[[slow]] Brief.")
    reg = world.app.sessions
    await reg.close(b.log_id)
    assert a.continued_by is None and "handoff called off" in called_off(a)
    await send(world, a, "back to work")
    await until(lambda: not a.in_turn, 8, "the turn ended")
    await reg.close(a.log_id)
    assert reg.reopen(a.log_id).log_id == a.log_id


async def test_a_restart_keeps_the_lock_and_the_archive_happens(world):
    a, b = await handed(world, "[[slow]] Brief.")
    log_a, log_b = a.log_id, b.log_id
    await world.restart()
    reg = world.app.sessions
    if log_a in reg.sessions:  # b may have answered before the restart cut it
        with pytest.raises(OpError) as e:
            await send(world, world.session(log_a), "still here")
        assert e.value.code == "handed_off"
    await turn(world.session(log_b), "go on")
    await until(lambda: log_a in reg.archived, 8, "archived after the restart")


async def monitor(world, a):
    params = {
        "description": "a long build",
        "done": "false",
        "progress": None,
        "source": None,
    }
    await world.app.registry.call("monitor.start", params, Caller("agent", a.log_id))


async def typed(world, a, line):
    r = await send(world, a, line)
    return world.session(r["log_id"])


def first_message(s) -> str:
    return next(str(e) for e in s.entries() if e["kind"] == "user")


async def test_handoff_line_sends_the_instruction_last(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    b = await typed(world, a, "/handoff implement this plan")
    assert b.spec.continues == a.log_id and a.continued_by == b.log_id
    assert b.spec.agent == a.spec.agent and b.spec.cwd == a.spec.cwd
    await until(lambda: any(e["kind"] == "user" for e in b.entries()), 8, "its prompt")
    said = first_message(b)
    assert said.rindex("implement this plan") > said.index("earlier work")
    assert "context had grown" not in said
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")


async def test_handoff_alone_works_with_no_brief(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    b = await typed(world, a, "/handoff")
    assert a.continued_by == b.log_id


async def test_handoff_with_another_agent(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    b = await typed(world, a, "/handoff --agent reviewer go on")
    assert b.spec.agent == "reviewer" and b.spec.cwd == a.spec.cwd
    assert b.spec.continues == a.log_id and b.group_key() == a.group_key()


async def test_a_flag_without_an_agent_keeps_the_rest_of_the_spec(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    b = await typed(world, a, "/handoff --effort high go on")
    assert b.spec.effort == "high"
    assert (b.spec.agent, b.spec.model) == (a.spec.agent, a.spec.model)


async def test_an_unknown_agent_leaves_the_session_unlocked(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    with pytest.raises(OpError) as e:
        await send(world, a, "/handoff --agent nobody go on")
    assert e.value.code == "unknown_agent" and a.continued_by is None
    await send(world, a, "back to work")


async def test_handoff_to_an_unknown_server_leaves_it_unlocked(world):
    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await send(world, a, "/handoff --agent opus@far go on")
    assert e.value.code == "unknown_server" and a.continued_by is None


async def test_handoff_on_an_asleep_session(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    await a.stop()
    b = await typed(world, a, "/handoff go on")
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")
    assert b.log_id in reg.sessions


async def test_handoff_is_refused_while_it_works(world):
    a = await world.spawn()
    await a.send("[[slow]] busy")
    await until(lambda: a.in_turn, 8, "the turn started")
    with pytest.raises(OpError) as e:
        await send(world, a, "/handoff go on")
    assert e.value.code == "busy"


async def test_a_monitor_refuses_the_handoff_by_name(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    await monitor(world, a)
    with pytest.raises(OpError) as e:
        await send(world, a, "/handoff go on")
    assert e.value.code == "has_wakers" and "1 monitor" in e.value.message
    assert a.continued_by is None


async def test_the_chip_is_refused_by_a_waker_too(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    await monitor(world, a)
    with pytest.raises(OpError) as e:
        await handoff(world, a)
    assert e.value.code == "has_wakers"
