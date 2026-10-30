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
    return " ".join(
        str(e.get("summary")) for e in s.entries() if e["kind"] == "system"
    )


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
