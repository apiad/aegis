# ruff: noqa: F811  (fixtures imported from test_acts_for)
"""Files a person reaches, opened as their Linux user (#555).

The real switch needs capabilities and real users (tests/multiuser/). Here the
mode is on for the app, ``as_user.become`` only records which user each runner
thread took, and ben is a fake account whose home is under the test's tmp_path.
"""

import pytest

from aegis import as_user
from aegis.ops import Caller, Principal

from .test_acts_for import BEN, ben_home, mu_world, recorded  # noqa: F401

ADMIN = Caller("user")


@pytest.fixture
def became(monkeypatch):
    """The users runner threads became, one entry per thread."""
    seen: list[str] = []
    monkeypatch.setattr(as_user, "become", lambda pw: seen.append(pw.pw_name))
    return seen


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch, ben_home, recorded, became):
    w = await mu_world(tmp_path, fake_claude, monkeypatch)
    yield w
    await w.stop()


def test_a_member_reaches_its_home_and_trashes_there(world, ben_home):
    p = world.app.place_of(BEN)
    assert (p.root, p.guard, p.trash, p.user) == (
        ben_home,
        ben_home / ".aegis" / "trash",
        ben_home / ".aegis",
        "ben",
    )


def test_an_admin_without_a_user_keeps_the_root_and_the_state(world):
    p = world.app.place_of(ADMIN)
    state = world.app.roots.state_root
    assert (p.root, p.guard, p.trash, p.user) == (world.root, state, state, None)


def test_an_admin_with_a_user_keeps_the_root_and_trashes_at_home(world, ben_home):
    world.app.principals.add("ana", "admin", linux_user="ben")
    p = world.app.place_of(Caller("user", principal=Principal("ana", "admin")))
    assert (p.root, p.guard, p.trash, p.user) == (
        world.root,
        world.app.roots.state_root,
        ben_home / ".aegis",
        "ben",
    )


def test_a_member_without_a_user_is_refused(world):
    with pytest.raises(PermissionError):
        world.app.place_of(Caller("user", principal=Principal("gone", "member")))


async def test_a_members_session_has_the_members_place(world, ben_home):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, BEN)
    p = world.app.place_of_session(r["log_id"])
    assert p.root == ben_home and p.user == "ben"
    assert world.app.place_of_session((await world.spawn()).log_id).user is None


async def test_outside_the_mode_everyone_is_the_server(tmp_path, fake_claude):
    from .test_agents import World

    w = await World(tmp_path, fake_claude).start()
    try:
        w.app.principals.add("ben", "member")
        p = w.app.place_of(BEN)
        assert (p.root, p.user) == (w.root, None)
    finally:
        await w.stop()
