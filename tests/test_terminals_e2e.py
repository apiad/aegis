"""Terminals end to end: the real app, its websocket, people as principals."""

import pytest
from starlette.testclient import TestClient

from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.roots import make_roots
from aegis.web import build_web

from .conftest import until
from .test_agents import World
from .test_web import ORIGIN, TOKEN, Conn

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
terminals:
  echo: {cmd: cat, cwd: .}
  sh: {cmd: sh, cwd: .}
  api: {cmd: "echo serving; sleep 100", cwd: ., restart: on-failure}
"""
BEN = Principal("ben", "member")


def as_(principal):
    return Caller("user", principal=principal)


def term_ops(conn, tid):
    return [
        o
        for m in conn.seen
        if m["t"] == "patch" and m["channel"] == f"term:{tid}"
        for o in m["ops"]
    ]


def test_a_browser_gets_output_and_on_reconnect_only_what_it_missed(
    tmp_path, fake_claude
):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    app = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            a = Conn(ws).hello()
            tid = a.call("term.spawn", profile="sh")["result"]["id"]
            ws.send_json({"t": "sub", "channel": f"term:{tid}"})
            first = a.until(
                lambda m: m["t"] == "snapshot" and m["channel"] == f"term:{tid}"
            )
            assert "tail" in first["data"]
            a.call("term.input", id=tid, data="echo hi-$((1+1))\r")
            a.until(lambda m: any("hi-2" in o.get("o", "") for o in term_ops(a, tid)))
            held = max(o["rev"] for o in term_ops(a, tid))
        # The first browser is gone; a second one makes the terminal print.
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            b = Conn(ws).hello()
            ws.send_json({"t": "sub", "channel": f"term:{tid}"})
            b.call("term.input", id=tid, data="seq 1 500; echo done-$((2+2))\r")
            b.until(lambda m: any("done-4" in o.get("o", "") for o in term_ops(b, tid)))
        # The first browser comes back with the rev it held.
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            back = Conn(ws).hello()
            ws.send_json({"t": "sub", "channel": f"term:{tid}", "since": held})
            snap = back.until(lambda m: m["t"] == "snapshot")["data"]
            assert "tail" not in snap
            revs = [o["rev"] for o in snap["delta"]]
            assert revs == sorted(set(revs)) and revs[0] > held
            markers = snap["rev"] - held - len(revs)  # the delta leaves them out
            assert 0 <= snap["rev"] - revs[-1] <= markers
            text = "".join(o.get("o", "") for o in snap["delta"])
            lines = set(text.split("\r\n"))
            assert all(str(n) in lines for n in range(1, 501)) and "done-4" in text


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    yield w
    await w.stop()


async def test_a_member_reads_any_terminal_but_types_only_into_their_own(world):
    call = world.app.registry.call
    admins = await call("term.spawn", {"profile": "echo"})
    with pytest.raises(OpError) as e:
        await call("term.input", {"id": admins["id"], "data": "x"}, as_(BEN))
    assert e.value.code == "not_owner"
    assert world.app.channels.subscribe(f"term:{admins['id']}", lambda m: None)
    mine = await call("term.spawn", {"profile": "echo"}, as_(BEN))
    assert mine["owner"] == "ben"
    await call("term.input", {"id": mine["id"], "data": "x"}, as_(BEN))
    await call("term.close", {"id": mine["id"]}, as_(BEN))


async def test_agents_have_no_terminal_operations_in_phase_1(world):
    s = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "term.spawn", {"profile": "echo"}, Caller("agent", log_id=s.log_id)
        )
    assert e.value.code == "not_for_agents"


async def test_terminal_cards_ride_the_sessions_channel(world):
    card = await world.app.registry.call("term.spawn", {"profile": "echo"})
    snap = world.app._resolve("sessions")()
    assert [c["id"] for c in snap if c.get("kind") == "term"] == [card["id"]]


async def test_after_a_restart_a_service_runs_and_a_shell_is_ended(world):
    call = world.app.registry.call
    shell = await call("term.spawn", {"profile": "echo"})
    api = await call("term.spawn", {"profile": "api"})
    await world.restart()
    call = world.app.registry.call  # the restarted app's, not the stopped one's
    terms = world.app.terminals
    assert terms.open[shell["id"]].meta["reason"] == "server_stopped"
    await until(lambda: terms.open[api["id"]].running, what="the service")
    await until(
        lambda: "serving" in terms.channel(api["id"], None)()["tail"], what="output"
    )
    with pytest.raises(OpError) as e:
        await call("term.input", {"id": shell["id"], "data": "x"})
    assert e.value.code == "not_running"
    await call("term.restart", {"id": shell["id"]})
    assert terms.open[shell["id"]].running
