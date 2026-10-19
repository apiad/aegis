"""What the browser tells the server for parking: which session it shows in a
visible page, and that someone started typing to a parked one."""

from starlette.testclient import TestClient

from aegis.app import App
from aegis.roots import make_roots
from aegis.web import build_web

from .conftest import until
from .test_agents import turn
from .test_links import Browser, pair  # noqa: F401  (the fixture)
from .test_park_e2e import world  # noqa: F401  (the fixture)
from .test_web import ORIGIN, TOKEN, Conn, project  # noqa: F401


def test_a_visible_page_counts_and_a_closed_socket_stops_counting(project, fake_claude):  # noqa: F811
    app = App(make_roots(project, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            conn = Conn(ws).hello()
            log_id = conn.call("session.spawn", agent="opus")["result"]["log_id"]
            ws.send_json({"t": "watch", "session": log_id, "visible": True})
            conn.call("session.list")  # a round trip: the watch was handled before it
            assert app.watchers.count(log_id) == 1
            ws.send_json({"t": "watch", "session": log_id, "visible": False})
            conn.call("session.list")
            assert app.watchers.count(log_id) == 0
            ws.send_json({"t": "watch", "session": log_id, "visible": True})
            conn.call("session.list")
        # The socket closed without a word, as a crashed browser's does.
        assert app.watchers.count(log_id) == 0


async def test_a_watch_on_a_linked_server_counts_there(pair):  # noqa: F811
    alpha, beta = pair
    s = await beta.spawn()
    async with Browser(alpha) as b:
        await b.send(t="watch", server="beta", session=s.log_id, visible=True)
        await until(lambda: beta.app.watchers.count(s.log_id) == 1, what="the watch")
        assert alpha.app.watchers.count(s.log_id) == 0
    # The browser's socket on alpha closed: alpha tells beta it stopped watching.
    await until(lambda: beta.app.watchers.count(s.log_id) == 0, what="the drop")


async def test_a_link_sends_its_watches_again_after_a_reconnect(pair):  # noqa: F811
    alpha, beta = pair
    s = await beta.spawn()
    link = alpha.app.links.get("beta")
    async with Browser(alpha) as b:
        await b.send(t="watch", server="beta", session=s.log_id, visible=True)
        await until(lambda: beta.app.watchers.count(s.log_id) == 1, what="the watch")
        # The link drops: beta forgets every browser behind it.
        await link._ws.close()
        await until(lambda: beta.app.watchers.count(s.log_id) == 0, what="the drop")
        await until(lambda: link.state == "linked", timeout=10, what="linked again")
        await until(lambda: beta.app.watchers.count(s.log_id) == 1, what="the resend")


async def test_warm_starts_a_parked_session_and_nothing_else(world):  # noqa: F811
    a = await world.spawn()
    await turn(a, "hello ana")
    await world.app.registry.call("session.warm", {"log_id": a.log_id})
    assert a.running  # an idle one is untouched
    await a.park(60)
    await world.app.registry.call("session.warm", {"log_id": a.log_id})
    assert a.running and a.status == "idle"


async def test_warm_starts_nothing_while_the_quota_gate_holds(world, monkeypatch):  # noqa: F811
    a = await world.spawn()
    await turn(a, "hello ana")
    await a.park(60)
    reg = world.app.sessions
    monkeypatch.setattr(reg, "quota_hold", lambda s: "the weekly window is spent")
    await world.app.registry.call("session.warm", {"log_id": a.log_id})
    assert not a.running and a.asleep_why == "idle"
