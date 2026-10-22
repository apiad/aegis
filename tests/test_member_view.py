# ruff: noqa: F811  (fixtures imported from test_acts_for)
"""What a member sees in multi-user mode (#566; the spec's section 08): only
what it owns. Someone else's thing answers as a thing that does not exist.
The mode is on for the app; the user switch itself is tests/multiuser/'s.
"""

import pwd

import pytest

from aegis import as_user, users
from aegis.ops import Caller, OpError, Principal

from .test_acts_for import BEN, mu_world, recorded  # noqa: F401

CARA = Caller("user", principal=Principal("cara", "member"))
ADMIN = Caller("user")


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Fake accounts ben and cara, with homes under tmp_path."""
    real = pwd.getpwnam
    made = {}
    for n, uid in (("ben", 1500), ("cara", 1501)):
        home = tmp_path / f"home-{n}"
        home.mkdir()
        made[n] = pwd.struct_passwd((n, "x", uid, uid, "", str(home), "/bin/sh"))
    monkeypatch.setattr(pwd, "getpwnam", lambda n: made.get(n) or real(n))
    monkeypatch.setattr(users, "allowed", lambda name, **kw: made[name])
    monkeypatch.setattr(as_user, "become", lambda pw: None)
    return made


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch, homes, recorded):
    w = await mu_world(tmp_path, fake_claude, monkeypatch)
    w.app.principals.add("cara", "member")
    yield w
    await w.stop()


async def call(world, op: str, params: dict, caller: Caller):
    return await world.app.registry.call(op, params, caller)


async def refused(world, op: str, params: dict, caller: Caller) -> OpError:
    with pytest.raises(OpError) as e:
        await call(world, op, params, caller)
    return e.value


async def spawn(world, caller: Caller) -> str:
    return (await call(world, "session.spawn", {"agent": "opus"}, caller))["log_id"]


async def test_another_members_session_answers_as_one_that_never_existed(world):
    theirs = await spawn(world, CARA)
    for op, params in (
        ("session.read", {"log_id": theirs, "ids": []}),
        ("session.send", {"log_id": theirs, "text": "hi"}),
        ("session.stop", {"log_id": theirs}),
        ("transcript.search", {"log_id": theirs, "q": "x"}),
    ):
        hidden = await refused(world, op, params, BEN)
        missing = await refused(
            world, op, {**params, "log_id": "20990101-000000-000000"}, BEN
        )
        assert (hidden.code, hidden.message.replace(theirs, "?")) == (
            missing.code,
            missing.message.replace("20990101-000000-000000", "?"),
        ), op


async def test_a_member_reaches_its_own_and_an_admin_reaches_everything(world):
    mine = await spawn(world, BEN)
    theirs = await spawn(world, CARA)
    await call(world, "session.read", {"log_id": mine, "ids": []}, BEN)
    await call(world, "session.read", {"log_id": theirs, "ids": []}, ADMIN)


async def test_an_agent_cannot_reach_another_members_session_by_handle(world):
    mine = await spawn(world, BEN)
    theirs = await spawn(world, CARA)
    handle = world.app.sessions.sessions[theirs].handle
    agent = Caller("agent", log_id=mine)
    hidden = await refused(world, "peer.read", {"target": handle}, agent)
    missing = await refused(world, "peer.read", {"target": "no-such-one"}, agent)
    assert hidden.code == missing.code == "no_session"
    assert handle not in hidden.message.replace(repr(handle), "")


async def test_outside_the_mode_a_member_reads_every_session(tmp_path, fake_claude):
    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        w.app.principals.add("ben", "member")
        theirs = (await w.spawn()).log_id
        await w.app.registry.call("session.read", {"log_id": theirs, "ids": []}, BEN)
    finally:
        await w.stop()


async def test_another_members_task_answers_as_no_task(world):
    mine = Caller("agent", log_id=await spawn(world, BEN))
    theirs = Caller("agent", log_id=await spawn(world, CARA))
    t = await call(
        world, "queue.enqueue", {"queue": "general", "payload": "hi"}, theirs
    )
    hidden = await refused(world, "task.status", {"task_id": t["task_id"]}, mine)
    missing = await refused(world, "task.status", {"task_id": "task-00000000"}, mine)
    assert (hidden.code, hidden.message.replace(t["task_id"], "?")) == (
        missing.code,
        missing.message.replace("task-00000000", "?"),
    )
    await call(world, "task.status", {"task_id": t["task_id"]}, theirs)


def watch(world, channel: str, caller: Caller) -> list[dict]:
    """Every message ``channel`` sends ``caller``'s subscription."""
    got: list[dict] = []
    world.app.channels.subscribe(channel, got.append, view=world.app.view_of(caller))
    return got


def ids(msgs: list[dict]) -> set:
    out = set()
    for m in msgs:
        rows = m.get("data") or []
        rows += [o.get("upsert") or {} for o in m.get("ops", [])]
        out |= {r.get("log_id") for r in rows if r.get("log_id")}
        out |= {o["remove"] for o in m.get("ops", []) if "remove" in o}
    return out


async def test_a_members_sessions_channel_holds_its_own(world):
    mine = await spawn(world, BEN)
    theirs = await spawn(world, CARA)
    got = watch(world, "sessions", BEN)
    assert mine in ids(got) and theirs not in ids(got)
    await call(world, "session.rename", {"log_id": theirs, "title": "x"}, CARA)
    await call(world, "session.close", {"log_id": theirs}, CARA)
    await call(world, "session.rename", {"log_id": mine, "title": "y"}, BEN)
    world.app._sessions_out._flush()
    assert theirs not in ids(got) and mine in ids(got)
    admin = watch(world, "sessions", ADMIN)
    assert mine in ids(admin)


async def test_another_members_entity_channels_do_not_exist_for_it(world):
    theirs = await spawn(world, CARA)
    for ch in (f"transcript:{theirs}", f"repos:{theirs}"):
        with pytest.raises(OpError) as e:
            watch(world, ch, BEN)
        assert e.value.code == "unknown_channel"
        watch(world, ch, CARA)
        watch(world, ch, ADMIN)


async def test_a_toast_from_another_members_agent_does_not_reach_it(world):
    theirs = await spawn(world, CARA)
    got = watch(world, "toasts", BEN)
    world.app.toast("cara's", source=world.app.sessions.sessions[theirs])
    world.app.toast("for everyone")
    texts = [o["toast"]["text"] for m in got for o in m.get("ops", [])]
    assert texts == ["for everyone"]
