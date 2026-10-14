"""Quiet sessions: no tab until the turn ends (#386)."""

import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, hold, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def meta_of(world, log_id: str) -> dict:
    """The session's meta file as boot would read it."""
    return json.loads(world.app.sessions.metas.path(log_id).read_text())


async def test_an_agent_quiets_a_waiting_session(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    said = await turn(a, mcp("session_quiet", handle=b.handle))
    assert not said.startswith("mcp error"), said
    assert b.quiet and b.wire()["quiet"] is True
    assert meta_of(world, b.log_id)["quiet"] is True


async def test_an_idle_session_cannot_be_quieted(world):
    a, b = await world.spawn(), await world.spawn()
    await turn(b, mcp("turn_end", attention="done", line="Done."))
    said = await turn(a, mcp("session_quiet", handle=b.handle))
    assert said.startswith("mcp error: not_running"), said
    assert not b.quiet


async def test_the_person_quiets_and_unquiets_by_log_id(world):
    b = await world.spawn()
    await hold(b)
    r = await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    assert r["quiet"] is True
    r = await world.app.registry.call("session.unquiet", {"log_id": b.log_id})
    assert r["quiet"] is False and not b.quiet


@pytest.mark.slow  # a restart
async def test_quiet_survives_a_restart(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await world.restart()
    assert world.session(b.log_id).quiet is True


@pytest.mark.parametrize("attention", ["done", "review", "needs_you"])
async def test_a_quiet_session_comes_back_when_its_turn_ends(world, attention):
    b = await world.spawn()
    held = await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await turn(b, mcp("turn_end", attention=attention, line="Over to you."))
    await until(lambda: not b.quiet, timeout=5, what="back in the tab bar")
    assert meta_of(world, b.log_id)["quiet"] is False


async def test_a_turn_that_ends_waiting_keeps_it_quiet(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await hold(b)  # a second turn that ends on a live monitor
    assert b.wire()["attention"] == "waiting" and b.quiet


async def test_a_session_that_quiets_itself_is_back_when_that_turn_ends(world):
    b = await world.spawn()
    await turn(b, mcp("session_quiet", handle=b.handle))
    await until(lambda: not b.quiet, timeout=5, what="back after its own turn")


async def test_a_session_whose_process_dies_comes_back(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await b.send("/exit 3")
    await until(lambda: not b.quiet, timeout=8, what="back after the exit")
