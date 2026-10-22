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


async def as_ben(world, op: str, params: dict):
    return await world.app.registry.call(op, params, BEN)


def get(world, url: str, **headers):
    from starlette.testclient import TestClient

    from aegis.web import build_web

    client = TestClient(build_web(world.app, "tok", {"testserver"}))
    return client.get(url, headers=headers)


async def test_the_files_view_of_a_member_is_its_home_on_its_runner(
    world, ben_home, became
):
    (ben_home / "notes.md").write_text("# mine\n")
    (world.root / "server-only.md").write_text("no\n")
    listed = await as_ben(world, "fs.list", {"dir": ""})
    names = [e["name"] for e in listed["entries"]]
    assert "notes.md" in names and "server-only.md" not in names
    assert became == ["ben"]  # one runner thread, which took ben's ids
    read = await as_ben(world, "fs.read", {"path": "notes.md"})
    assert read["text"] == "# mine\n"
    await as_ben(world, "fs.write", {"path": "new.txt", "content": "x", "base": None})
    assert (ben_home / "new.txt").read_text() == "x"
    await as_ben(world, "fs.mkdir", {"path": "d"})
    await as_ben(world, "fs.move", {"path": "new.txt", "to": "d/new.txt"})
    assert (ben_home / "d" / "new.txt").exists()
    found = await as_ben(world, "fs.find", {})
    assert "notes.md" in [rel for rel, _ in found["files"]]
    assert set(became) == {"ben"}


async def test_a_members_delete_goes_to_its_own_trash(world, ben_home):
    (ben_home / "old.txt").write_text("bye")
    t = await as_ben(world, "fs.delete", {"path": "old.txt"})
    assert not (ben_home / "old.txt").exists()
    assert (ben_home / ".aegis" / "trash" / t["trash_id"] / "item").exists()
    assert not (world.app.roots.state_root / "trash" / t["trash_id"]).exists()
    with pytest.raises(Exception, match="aegis's own state"):
        await as_ben(world, "fs.list", {"dir": ".aegis/trash"})
    await as_ben(world, "fs.restore", {"trash_id": t["trash_id"]})
    assert (ben_home / "old.txt").read_text() == "bye"


async def test_a_members_ticket_serves_its_file_with_a_range(world, ben_home):
    (ben_home / "v.mp4").write_bytes(bytes(range(256)) * 4)
    url = (await as_ben(world, "fs.ticket", {"path": "v.mp4"}))["url"]
    whole = get(world, url)
    assert whole.status_code == 200 and whole.content == bytes(range(256)) * 4
    part = get(world, url, range="bytes=10-19")
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/1024"
    tail = get(world, url, range="bytes=-4")
    assert tail.content == bytes(range(252, 256))
    assert get(world, url, range="bytes=2000-").status_code == 416


async def test_a_members_html_ticket_keeps_the_sandbox(world, ben_home):
    (ben_home / "r.html").write_text("<p>hi</p>")
    url = (await as_ben(world, "fs.ticket", {"path": "r.html"}))["url"]
    r = get(world, url)
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    assert r.headers["cache-control"] == "no-store"
    assert b"<p>hi</p>" in r.content


async def test_a_ticket_reads_in_its_issuers_place(world, ben_home):
    (world.root / "admin.pdf").write_bytes(b"%PDF admin")
    (ben_home / "admin.pdf").write_bytes(b"%PDF ben")
    admin = await world.app.registry.call("fs.ticket", {"path": "admin.pdf"}, ADMIN)
    ben = await as_ben(world, "fs.ticket", {"path": "admin.pdf"})
    assert get(world, admin["url"]).content == b"%PDF admin"
    assert get(world, ben["url"]).content == b"%PDF ben"


async def test_fs_open_is_refused_in_the_mode(world, ben_home):
    (world.root / "a.txt").write_text("a")
    with pytest.raises(Exception, match="shared server"):
        await world.app.registry.call(
            "fs.open", {"path": "a.txt"}, Caller("user", desktop=True)
        )
