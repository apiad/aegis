"""One lifecycle vocabulary (#479): a session with no process is asleep, and
says why: slept (a Stop), idle (a park), exited (the harness ended on its own)
or server (the server restarted under it)."""

import json
import os
import signal

import pytest

from aegis import agent_ops
from aegis.session import asleep_from

from .conftest import until
from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_stop_and_park_both_leave_a_session_asleep_with_their_reason(world):
    a = await world.spawn()
    await turn(a, "hello")
    await world.app.registry.call("session.stop", {"log_id": a.log_id})
    assert (a.status, a.asleep_why) == ("asleep", "slept")
    assert a.entries()[-1]["summary"] == "asleep"
    b = await world.spawn()
    await turn(b, "hello")
    assert await b.park(1200)
    assert (b.status, b.asleep_why) == ("asleep", "idle")
    assert b.entries()[-1]["summary"] == "asleep after 20 min idle"
    assert b.wire()["asleep_why"] == "idle"
    await turn(b, "hello again")
    assert (b.status, b.asleep_why) == ("idle", None)


async def test_an_exit_and_a_server_restart_are_asleep_too(world):
    a = await world.spawn()
    await turn(a, "hello")
    os.kill(a.pid, signal.SIGKILL)
    await until(lambda: a.status == "asleep", timeout=5, what="the exit")
    assert a.asleep_why == "exited"
    b = await world.spawn()
    await turn(b, "hello")
    log_id = b.log_id
    await world.restart()
    b = world.session(log_id)
    assert (b.status, b.asleep_why) == ("asleep", "server")
    a = world.session(a.log_id)
    assert (a.status, a.asleep_why) == ("asleep", "exited")


def test_an_old_meta_reads_as_asleep():
    assert asleep_from("stopped", None) == ("asleep", "slept")
    assert asleep_from("parked", None) == ("asleep", "idle")
    assert asleep_from("idle", None) == ("asleep", "server")
    assert asleep_from("working", None) == ("asleep", "server")
    assert asleep_from("asleep", "exited") == ("asleep", "exited")


async def test_old_metas_on_disk_load_as_asleep(world):
    a, b = await world.spawn(), await world.spawn()
    await world.stop()
    for s, old in ((a, "stopped"), (b, "parked")):
        path = world.app.sessions.metas.path(s.log_id)
        m = json.loads(path.read_text())
        m.pop("asleep_why", None)
        path.write_text(json.dumps({**m, "last_status": old, "pid": None}))
    await world.start()
    got = [
        (world.session(s.log_id).status, world.session(s.log_id).asleep_why)
        for s in (a, b)
    ]
    assert got == [("asleep", "slept"), ("asleep", "idle")]


def test_session_list_keeps_a_linked_servers_old_states():
    far = [
        {"handle": "old-stop", "state": "stopped"},
        {"handle": "old-park", "state": "parked"},
        {"handle": "new-one", "state": "asleep", "asleep_why": "exited"},
        {"handle": "liar-one", "state": "asleep", "asleep_why": "MARKER"},
        {"handle": "busy-one", "state": "working"},
    ]
    assert [agent_ops.far_entry(e, "beta") for e in far] == [
        {
            "handle": "old-stop",
            "server": "beta",
            "state": "asleep",
            "asleep_why": "slept",
        },
        {
            "handle": "old-park",
            "server": "beta",
            "state": "asleep",
            "asleep_why": "idle",
        },
        {
            "handle": "new-one",
            "server": "beta",
            "state": "asleep",
            "asleep_why": "exited",
        },
        {"handle": "liar-one", "server": "beta", "state": "asleep", "asleep_why": None},
        {"handle": "busy-one", "server": "beta", "state": "working"},
    ]
