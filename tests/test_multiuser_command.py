# ruff: noqa: F811  (fixtures imported from test_member_view and test_commands)
"""/multiuser: the one turn it delivers, to an admin only (#603; the spec's
section 10)."""

import pytest

from aegis import multiuser
from aegis.ops import OpError

from .conftest import until
from .test_acts_for import BEN, recorded  # noqa: F401
from .test_commands import app, send  # noqa: F401
from .test_commands import spawn as spawn_here
from .test_member_view import ADMIN, homes, world  # noqa: F401
from .test_member_view import call, spawn


def test_off_the_turn_sends_the_agent_through_the_whole_page():
    header, body = multiuser.turn("ana and ben", False, "aegis 2.12.0", "now")
    assert header == "> from multiuser · now"
    assert "The person typed: /multiuser ana and ben" in body
    assert "help_read multi-user" in body and "in full" in body
    assert "before changing anything" in body
    assert "run nothing until they say yes" in body
    assert body.rstrip().endswith("aegis 2.12.0")


def test_on_the_turn_starts_at_adding_a_person_and_the_checks():
    _, body = multiuser.turn("", True, "facts", "now")
    assert "The person typed /multiuser with no text" in body
    assert "already in multi-user mode" in body
    for section in ("add-a-person", "remove-a-person", "check"):
        assert f"help_read multi-user {section}" in body
    assert "in full" not in body


def test_facts_name_the_servers_user_and_the_mode():
    line = multiuser.facts(True)
    assert line.startswith("aegis ") and "multi-user mode: on" in line
    assert "server user: " in line
    assert "multi-user mode: off" in multiuser.facts(False)


def inbox(s) -> list[str]:
    return [
        e["md"]
        for e in s.entries()
        if e["kind"] == "inbox" and "> from multiuser" in (e.get("md") or "")
    ]


async def test_an_admin_gets_one_turn_and_no_new_session(app):
    lid = await spawn_here(app)
    s = app.sessions.sessions[lid]
    before = len(app.sessions.sessions)
    assert await send(app, lid, "/multiuser") == {"status": "delivered"}
    await until(lambda: inbox(s), timeout=5)
    (md,) = inbox(s)
    assert "help_read multi-user" in md and "multi-user mode: off" in md
    assert len(app.sessions.sessions) == before


async def test_in_the_mode_an_admin_starts_at_adding_a_person(world):
    lid = await spawn(world, ADMIN)
    s = world.app.sessions.sessions[lid]
    r = await call(world, "session.send", {"log_id": lid, "text": "/multiuser"}, ADMIN)
    assert r == {"status": "delivered"}
    await until(lambda: inbox(s), timeout=5)
    (md,) = inbox(s)
    assert "help_read multi-user add-a-person" in md
    assert "multi-user mode: on" in md


async def test_a_member_is_refused_and_its_agent_gets_nothing(world):
    lid = await spawn(world, BEN)
    s = world.app.sessions.sessions[lid]
    with pytest.raises(OpError) as e:
        await call(world, "session.send", {"log_id": lid, "text": "/multiuser"}, BEN)
    assert e.value.code == "not_for_members"
    assert not inbox(s) and not any(
        m["header"].startswith("> from multiuser") for m in s.held
    )
