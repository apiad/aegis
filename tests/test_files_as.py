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


async def ben_session(world):
    r = await world.app.registry.call("session.spawn", {"agent": "opus"}, BEN)
    return r["log_id"]


async def test_a_members_agent_sends_a_file_read_on_its_runner(
    world, ben_home, monkeypatch
):
    log_id = await ben_session(world)
    (ben_home / "chart.txt").write_text("42")
    ran: list[tuple[str | None, str]] = []
    real = as_user.Runner.__call__

    async def spy(self, fn, *a, **kw):
        ran.append((self.user, getattr(fn, "__name__", "")))
        return await real(self, fn, *a, **kw)

    monkeypatch.setattr(as_user.Runner, "__call__", spy)
    agent = Caller("agent", log_id=log_id)
    sent = await world.app.registry.call("file.send", {"paths": ["chart.txt"]}, agent)
    assert ("ben", "open_all") in ran
    file_id = sent[0]["url"].split("/")[2]
    stored = world.app.roots.state_root / "files" / file_id / "chart.txt"
    assert stored.read_text() == "42"


async def test_a_members_attachment_lands_in_its_home_inbox(world, ben_home, became):
    import base64

    log_id = await ben_session(world)
    s = world.app.sessions.sessions[log_id]
    box = ben_home / ".aegis" / "inbox" / log_id
    assert s.inbox == box and box.is_dir() and box.stat().st_mode & 0o777 == 0o700
    uid = await as_ben(
        world, "attachment.begin", {"log_id": log_id, "name": "a.txt", "size": 2}
    )
    up = uid["upload_id"] if isinstance(uid, dict) else uid
    data = base64.b64encode(b"hi").decode()
    await as_ben(
        world,
        "attachment.put",
        {"log_id": log_id, "upload_id": up, "offset": 0, "data": data},
    )
    await as_ben(
        world, "session.send", {"log_id": log_id, "text": "see", "attachments": [up]}
    )
    landed = list(box.glob("*-a.txt"))
    assert [p.read_bytes() for p in landed] == [b"hi"]
    assert not (world.app.roots.state_root / "inbox" / log_id / "a.txt").exists()
    assert not list((world.app.roots.state_root / "inbox" / log_id).glob("*-a.txt"))


async def test_a_members_artifact_draft_lives_in_its_home(world, ben_home):
    log_id = await ben_session(world)
    agent = Caller("agent", log_id=log_id)
    made = await world.app.registry.call("artifact.create", {"title": "Pick"}, agent)
    path = ben_home / ".aegis" / "artifacts" / made["id"] / "index.html"
    assert made["path"] == str(path) and path.read_text() == made["html"]
    state = world.app.roots.state_root
    assert not (state / "artifacts" / made["id"]).exists()
    gone = await world.app.registry.call("artifact.create", {"title": "No"}, agent)
    await world.app.registry.call("artifact.close", {"id": gone["id"]}, agent)
    assert not (ben_home / ".aegis" / "artifacts" / gone["id"]).exists()


def test_a_failed_copy_closes_the_opened_file_once(tmp_path, monkeypatch):
    import os

    from aegis import files

    src = tmp_path / "a.txt"
    src.write_text("a")
    state = tmp_path / "state"
    (state / "files").mkdir(parents=True)
    (state / "files").chmod(0o500)  # the copy cannot be written
    twice: list[int] = []
    real = os.close

    def close(fd):
        try:
            os.fstat(fd)
        except OSError:
            twice.append(fd)  # already closed: this number may be another file now
        real(fd)

    monkeypatch.setattr(os, "close", close)
    opened = files.open_all([src])
    fd = opened[0].fd
    try:
        with pytest.raises(files.FileError):
            files.store_all(state, [src], opened)
    finally:
        (state / "files").chmod(0o700)
    assert twice == []
    with pytest.raises(OSError):
        os.fstat(fd)


async def test_a_stream_that_never_starts_still_closes_its_file(tmp_path):
    import os

    from aegis.web import _FdStream

    (tmp_path / "f").write_bytes(b"x")
    fd = os.open(tmp_path / "f", os.O_RDONLY)

    async def body():
        yield b"x"

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("the client is gone")

    with pytest.raises(OSError):
        await _FdStream(fd, body())({"type": "http"}, receive, send)
    with pytest.raises(OSError):
        os.fstat(fd)


async def test_an_inverted_range_is_ignored(world, ben_home):
    (ben_home / "v.bin").write_bytes(b"0123456789")
    url = (await as_ben(world, "fs.ticket", {"path": "v.bin"}))["url"]
    r = get(world, url, range="bytes=5-3")
    assert r.status_code == 200 and r.content == b"0123456789"


async def test_a_member_without_a_user_is_forbidden_not_internal(world):
    from aegis.ops import OpError

    gone = Caller("user", principal=Principal("gone", "member"))
    with pytest.raises(OpError) as e:
        await world.app.registry.call("fs.list", {"dir": ""}, gone)
    assert e.value.code == "forbidden"


async def test_outside_the_mode_a_recorded_user_changes_no_path(tmp_path, fake_claude):
    import dataclasses

    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        s = await w.spawn()
        s.spec = dataclasses.replace(s.spec, linux_user="aegis-no-such-user")
        assert s.inbox == w.app.roots.state_root / "inbox" / s.log_id
        assert s.drafts == w.app.roots.state_root
        assert w.app._deliver(s) is None
    finally:
        await w.stop()
