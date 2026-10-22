# ruff: noqa: F811  (fixtures imported from test_member_view and test_acts_for)
"""config_suggest under multi-user: a member's agent may suggest, only an
admin applies (#612)."""

import json

import pytest

from aegis.ops import OpError

from .test_acts_for import BEN, recorded  # noqa: F401
from .test_agents import inbox, mcp, turn
from .test_member_view import ADMIN, call, homes, spawn, world  # noqa: F401


async def test_a_member_cannot_apply_and_its_agent_is_not_woken(world):
    lid = await spawn(world, BEN)
    s = world.app.sessions.sessions[lid]
    base = world.app.config.path.read_text().replace("  broken: {agent: opus}\n", "")
    world.app.config.path.write_text(base)
    text = base + "# a member's idea\n"
    said = await turn(s, mcp("config_suggest", yaml=text, why="w"))
    assert said.startswith("mcp ok: "), said
    id = json.loads(said.removeprefix("mcp ok: "))["id"]
    with pytest.raises(OpError) as e:
        await call(world, "config.suggestion.apply", {"log_id": lid, "id": id}, BEN)
    assert e.value.code == "not_for_members"
    assert next(x for x in s.entries() if x["id"] == id)["status"] == "pending"
    assert not [m for m in inbox(s) if f"config:{id}" in (m.get("md") or "")]
    assert "# a member's idea" not in world.app.config.path.read_text()
    assert (
        await call(world, "config.suggestion.apply", {"log_id": lid, "id": id}, ADMIN)
        == "ok"
    )
    assert world.app.config.path.read_text() == text
