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
