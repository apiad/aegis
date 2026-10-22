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


NEW = {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": "triage"}


async def test_a_members_lists_hold_its_own(world):
    mine = await spawn(world, BEN)
    theirs = await spawn(world, CARA)
    ben = Caller("agent", log_id=mine)
    cara = Caller("agent", log_id=theirs)
    theirs_handle = world.app.sessions.sessions[theirs].handle
    listed = await call(world, "session.list", {}, ben)
    assert theirs_handle not in [s["handle"] for s in listed]
    assert world.app.sessions.sessions[mine].handle in [s["handle"] for s in listed]
    await call(world, "schedule.create", {"name": "ben-sched", **NEW}, ben)
    await call(world, "schedule.create", {"name": "cara-sched", **NEW}, cara)
    names = [s["name"] for s in await call(world, "schedule.list", {}, ben)]
    assert "ben-sched" in names and "cara-sched" not in names
    hidden = await refused(world, "schedule.pause", {"name": "cara-sched"}, ben)
    assert hidden.code == "no_schedule"
    for who, text in ((ben, "ben's decision"), (cara, "cara's decision")):
        await call(world, "journal.note", {"text": text, "tag": "decision"}, who)
    world.app.journal.flush()
    said = await call(world, "journal.search", {"kind": ["note"]}, ben)
    assert "ben's decision" in said and "cara's decision" not in said
    said = await call(world, "journal.search", {"kind": ["note"]}, ADMIN)
    assert "ben's decision" in said and "cara's decision" in said


async def test_a_members_archive_holds_its_own(world):
    mine = await spawn(world, BEN)
    theirs = await spawn(world, CARA)
    await call(world, "session.close", {"log_id": mine}, BEN)
    await call(world, "session.close", {"log_id": theirs}, CARA)
    got = await call(world, "archive.list", {"limit": 50}, BEN)
    listed = [m["log_id"] for m in got["items"]]
    assert mine in listed and theirs not in listed and got["total"] == len(listed)
    every = await call(world, "archive.list", {"limit": 50}, ADMIN)
    assert {mine, theirs} <= {m["log_id"] for m in every["items"]}


async def test_a_schedule_cannot_fire_into_another_members_session(world):
    ben = Caller("agent", log_id=await spawn(world, BEN))
    theirs = await spawn(world, CARA)
    handle = world.app.sessions.sessions[theirs].handle
    fire = {"target": "existing", "session": handle}
    d = {"name": "into-cara", "clock": {"every": "5m"}, "fire": fire, "prompt": "x"}
    hidden = await refused(world, "schedule.create", d, ben)
    gone = await refused(
        world,
        "schedule.create",
        {**d, "fire": {**fire, "session": "no-such-one"}},
        ben,
    )
    assert hidden.code == gone.code == "no_session"


async def test_pushes_reach_only_the_devices_of_who_may_see(world):
    sender = world.app.push.sender
    theirs = await spawn(world, CARA)
    rows = [
        {"id": "dev-ben", "owner": "ben", "sources": ["needs_you"]},
        {"id": "dev-cara", "owner": "cara", "sources": ["needs_you"]},
        {"id": "dev-admin", "owner": world.app.user, "sources": ["needs_you"]},
    ]
    world.app.push.subscriptions.list = lambda: rows
    sender._running = True
    sender._lane = lambda sid: __import__("asyncio").sleep(0)
    sender._queue_for("needs_you", {"key": theirs, "title": "t", "body": "b"})
    assert set(sender._pending) == {"dev-cara", "dev-admin"}


async def test_a_schedules_toast_reaches_its_owner_and_the_count_is_honest(world):
    got_ben = watch(world, "toasts", BEN)
    got_cara = watch(world, "toasts", CARA)
    n = world.app.toast("Schedule x is paused", "error", owner="cara")
    texts = lambda got: [o["toast"]["text"] for m in got for o in m.get("ops", [])]  # noqa: E731
    assert texts(got_ben) == [] and texts(got_cara) == ["Schedule x is paused"]
    assert n == 1


async def test_a_member_cannot_hand_off_to_a_linked_server(world):
    ben = Caller("agent", log_id=await spawn(world, BEN))
    e = await refused(world, "peer.handoff", {"target": "x@far", "context": "hi"}, ben)
    assert e.code == "not_for_members"  # as outside the mode


FLOW = "id: {id}\ntitle: T\nsay: S.\nroot:\n  seq:\n    - {{sh: 'true', say: One.}}\n  say: Done.\n"


async def test_who_saved_a_workflow_is_a_members_own_business(world):
    ben = Caller("agent", log_id=await spawn(world, BEN))
    cara = Caller("agent", log_id=await spawn(world, CARA))
    for who, wid in ((ben, "bens-flow"), (cara, "caras-flow")):
        await call(world, "workflow.save", {"definition": FLOW.format(id=wid)}, who)
    rows = {r["id"]: r for r in await call(world, "workflow.list", {}, ben)}
    assert "saved_by" in rows["bens-flow"] and "saved_by" not in rows["caras-flow"]
    hidden = await refused(world, "workflow.delete", {"id": "caras-flow"}, ben)
    missing = await refused(world, "workflow.delete", {"id": "no-such-flow"}, ben)
    assert (hidden.code, hidden.message.replace("caras-flow", "?")) == (
        missing.code,
        missing.message.replace("no-such-flow", "?"),
    )
    await call(world, "workflow.delete", {"id": "bens-flow"}, ben)
