"""Links: one aegis server as a client of another (links.py).

Two real servers in this process, alpha and beta, each an App behind uvicorn on
its own port, with the fake claude. Alpha links beta. A browser here is a raw
websocket speaking the client protocol to alpha.
"""

import asyncio
import json
import logging
import socket
import stat
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve as ws_serve

from aegis import links
from aegis.app import App
from aegis.ops import OpError
from aegis.roots import make_roots
from aegis.web import PROTO, build_web

from .conftest import until

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
queues:
  general: {agent: opus, max_parallel: 1}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Node:
    """One aegis server: an App behind uvicorn, on a port that survives restarts."""

    def __init__(self, root: Path, name: str, fake: str):
        self.root, self.name, self.fake = root, name, fake
        root.mkdir(parents=True, exist_ok=True)
        (root / ".aegis.yaml").write_text(CONFIG)
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.token = f"token-of-{name}"
        self.app: App | None = None
        self._server = None
        self._task = None

    async def start(self) -> "Node":
        self.app = App(
            make_roots(self.root, None),
            claude_bin=self.fake,
            base_url=self.base,
            interrupt_timeout=1,
            server_name=self.name,
        )
        web = build_web(self.app, self.token, {f"127.0.0.1:{self.port}"})
        self._server = uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._task = asyncio.create_task(self._server.serve())
        await until(lambda: self._server.started, timeout=10, what=f"{self.name} up")
        return self

    async def stop(self) -> None:
        self._server.should_exit = True
        await asyncio.wait_for(self._task, 20)

    async def spawn(self, **params):
        r = await self.app.registry.call("session.spawn", {"agent": "opus", **params})
        return self.app.sessions.sessions[r["log_id"]]


class Browser:
    """A raw client socket on a node: hello, calls, and every frame it got."""

    def __init__(self, node: Node):
        self.node = node
        self.frames: list[dict] = []
        self.n = 0

    async def __aenter__(self) -> "Browser":
        self.ws = await connect(
            f"ws://127.0.0.1:{self.node.port}/ws", origin=self.node.base
        )
        await self.ws.send(
            json.dumps({"t": "hello", "token": self.node.token, "proto": PROTO})
        )
        self.welcome = json.loads(await self.ws.recv())
        self._reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc) -> None:
        self._reader.cancel()
        await self.ws.close()

    async def _read(self) -> None:
        async for raw in self.ws:
            self.frames.append(json.loads(raw))

    async def send(self, **msg) -> None:
        await self.ws.send(json.dumps(msg))

    async def call(self, op: str, server: str | None = None, **params) -> dict:
        self.n += 1
        n = self.n
        msg = {"t": "call", "id": n, "op": op, "params": params}
        if server:
            msg["server"] = server
        await self.send(**msg)
        await until(
            lambda: any(
                f.get("t") == "reply" and f.get("id") == n for f in self.frames
            ),
            timeout=10,
            what=f"the reply to {op}",
        )
        return next(
            f for f in self.frames if f.get("t") == "reply" and f.get("id") == n
        )

    def of(self, channel: str, server: str | None = None) -> list[dict]:
        return [
            f
            for f in self.frames
            if f.get("channel") == channel and f.get("server") == server
        ]


@pytest.fixture
async def pair(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    alpha.app.links.add("beta", beta.base, beta.token)
    await until(
        lambda: alpha.app.links.get("beta").state == "linked",
        timeout=10,
        what="the link",
    )
    yield alpha, beta
    await alpha.stop()
    await beta.stop()


# -- the link ---------------------------------------------------------------------
async def test_a_link_comes_up_and_names_the_far_server(pair):
    alpha, beta = pair
    (w,) = alpha.app.links.wire()
    assert w["name"] == "beta" and w["state"] == "linked" and w["proto"] == PROTO
    assert "token" not in w


async def test_a_browser_on_alpha_calls_and_subscribes_on_beta(pair):
    alpha, beta = pair
    async with Browser(alpha) as b:
        assert b.welcome["server"] == "alpha"
        r = await b.call("agents.list", server="beta")
        assert r["server"] == "beta" and [a["name"] for a in r["result"]["agents"]] == [
            "opus"
        ]
        await b.send(t="sub", server="beta", channel="sessions")
        await until(lambda: b.of("sessions", "beta"), what="beta's sessions snapshot")
        assert b.of("sessions", "beta")[0]["t"] == "snapshot"
        r = await b.call("session.spawn", server="beta", agent="opus")
        lid = r["result"]["log_id"]
        assert (
            lid in beta.app.sessions.sessions and lid not in alpha.app.sessions.sessions
        )
        await until(
            lambda: any(
                op.get("upsert", {}).get("log_id") == lid
                for f in b.of("sessions", "beta")
                if f["t"] == "patch"
                for op in f["ops"]
            ),
            what="the upsert from beta",
        )
        assert all("sid" not in f for f in b.frames)
        # The browser's own server is untouched by any of it.
        assert alpha.app.sessions.open_sessions() == []


async def test_an_unknown_server_is_refused(pair):
    alpha, _ = pair
    async with Browser(alpha) as b:
        r = await b.call("agents.list", server="gamma")
        assert r["error"]["code"] == "unknown_server"


async def test_two_subscriptions_to_one_channel_are_independent(pair):
    alpha, beta = pair
    async with Browser(alpha) as one, Browser(alpha) as two:
        for b in (one, two):
            await b.send(t="sub", server="beta", channel="sessions")
            await until(lambda b=b: b.of("sessions", "beta"), what="a snapshot")
        await one.send(t="unsub", server="beta", channel="sessions")
        await asyncio.sleep(0.2)
        await beta.spawn()
        await until(
            lambda: any(f["t"] == "patch" for f in two.of("sessions", "beta")),
            what="the patch on the subscription still open",
        )
        assert [f["seq"] for f in two.of("sessions", "beta")][:2] == [0, 1]
        assert not any(f["t"] == "patch" for f in one.of("sessions", "beta"))


async def test_the_link_reconnects_and_says_so(pair):
    alpha, beta = pair
    async with Browser(alpha) as b:
        await b.send(t="sub", channel="links")
        await beta.stop()
        await until(
            lambda: alpha.app.links.get("beta").state == "offline",
            timeout=10,
            what="offline",
        )
        r = await b.call("agents.list", server="beta")
        assert r["error"]["code"] == "server_offline"
        await beta.start()
        await until(
            lambda: alpha.app.links.get("beta").state == "linked",
            timeout=15,
            what="linked again",
        )
        states = [
            op["set"][0]["state"]
            for f in b.of("links")
            if f["t"] == "patch"
            for op in f["ops"]
        ]
        assert "offline" in states and states[-1] == "linked"


async def test_a_wrong_token_stops_retrying(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, "not-the-token")
        link = alpha.app.links.get("beta")
        await until(
            lambda: link.state == "unauthorized", timeout=10, what="unauthorized"
        )
        attempts = link.attempts
        await asyncio.sleep(2)  # the first retry would come after 1 s
        assert link.attempts == attempts
    finally:
        await alpha.stop()
        await beta.stop()


async def test_a_name_mismatch_keeps_the_link_down(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("gamma", beta.base, beta.token)
        link = alpha.app.links.get("gamma")
        await until(lambda: link.state == "mismatch", timeout=10, what="mismatch")
        assert "beta" in link.error and "gamma" in link.error
    finally:
        await alpha.stop()
        await beta.stop()


async def test_beta_cannot_call_or_subscribe_down_the_link(
    tmp_path, fake_claude, caplog
):
    """A hostile far end: it answers the hello, then sends what a client would."""
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    port = _free_port()

    async def hostile(ws):
        await ws.recv()  # the hello
        await ws.send(json.dumps({"t": "welcome", "proto": PROTO, "server": "evil"}))
        for frame in (
            {"t": "call", "id": 1, "op": "session.spawn", "params": {"agent": "opus"}},
            {"t": "sub", "channel": "sessions"},
            {"t": "hello", "token": alpha.token, "proto": PROTO},
            {"t": "nonsense"},
        ):
            await ws.send(json.dumps(frame))
        await asyncio.sleep(1)

    calls: list = []
    real = alpha.app.registry.call

    async def spy(op, raw, caller=None):
        calls.append(op)
        return await real(op, raw, caller) if caller else await real(op, raw)

    alpha.app.registry.call = spy  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="aegis.links")
    async with ws_serve(hostile, "127.0.0.1", port):
        alpha.app.links.add("evil", f"http://127.0.0.1:{port}", "x")
        await until(
            lambda: "dropped" in caplog.text, timeout=10, what="the drop logged"
        )
        await asyncio.sleep(0.5)
    try:
        assert calls == [] and alpha.app.sessions.open_sessions() == []
        for kind in ("call", "sub", "hello", "nonsense"):
            assert f"a {kind} frame" in caplog.text
    finally:
        await alpha.stop()


async def test_links_json_is_0600_and_tokens_never_reach_the_wire(pair):
    alpha, beta = pair
    path = alpha.app.roots.state_root / "links.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert beta.token in path.read_text()
    async with Browser(alpha) as b:
        listed = await b.call("link.list")
        await b.send(t="sub", channel="links")
        await until(lambda: b.of("links"), what="the links snapshot")
    assert beta.token not in json.dumps(listed) + json.dumps(b.frames)


async def test_link_add_checks_the_far_server_and_link_remove_forgets_it(
    pair, tmp_path, fake_claude
):
    alpha, beta = pair
    async with Browser(alpha) as b:
        r = await b.call("link.add", url=beta.base, token="wrong")
        assert r["error"]["code"] == "link_refused"
        r = await b.call("link.add", url=beta.base, token=beta.token)
        assert r["error"]["code"] == "bad_link"  # beta is linked already
        r = await b.call("link.remove", name="beta")
        assert "error" not in r and alpha.app.links.get("beta") is None
        r = await b.call("link.add", url=beta.base, token=beta.token)
        assert r["result"]["name"] == "beta"
        await until(
            lambda: alpha.app.links.get("beta").state == "linked", what="relinked"
        )


async def test_a_link_to_itself_is_refused(pair):
    alpha, _ = pair
    with pytest.raises(OpError) as e:
        alpha.app.links.add("alpha", alpha.base, alpha.token)
    assert e.value.code == "bad_link"


async def test_an_edit_to_links_json_reaches_a_running_server(pair):
    alpha, beta = pair
    store = links.LinkStore(alpha.app.roots.state_root / "links.json")
    store.save([])
    await until(
        lambda: alpha.app.links.get("beta") is None, timeout=5, what="the link dropped"
    )
    store.save(
        [{"name": "beta", "url": beta.base, "token": beta.token, "added": "now"}]
    )
    await until(
        lambda: getattr(alpha.app.links.get("beta"), "state", None) == "linked",
        timeout=10,
        what="the link back",
    )


async def test_a_link_socket_cannot_open_files_on_the_desktop(pair):
    alpha, beta = pair
    r = await alpha.app.links.get("beta").call_raw(
        "file.open", {"file_id": "x", "name": "y"}
    )
    assert r["error"]["code"] == "not_local"


async def test_a_socket_with_no_origin_must_say_link(pair):
    _, beta = pair
    async with connect(f"ws://127.0.0.1:{beta.port}/ws") as ws:
        await ws.send(json.dumps({"t": "hello", "token": beta.token, "proto": PROTO}))
        with pytest.raises(Exception):
            await ws.recv()
        assert ws.close_code == 4403
    async with connect(f"ws://127.0.0.1:{beta.port}/ws") as ws:
        hello = {
            "t": "hello",
            "token": "wrong",
            "proto": PROTO,
            "link": {"server": "x", "user": "u"},
        }
        await ws.send(json.dumps(hello))
        with pytest.raises(Exception):
            await ws.recv()
        assert ws.close_code == 4401


async def test_a_link_socket_does_not_relay(pair, tmp_path, fake_claude):
    alpha, beta = pair
    r = await alpha.app.links.get("beta").call_raw("agents.list", {}, server="alpha")
    assert r["error"]["code"] == "not_relayed"


async def test_a_sent_file_is_streamed_through_via(pair, tmp_path):
    alpha, beta = pair
    s = await beta.spawn()
    f = tmp_path / "report.html"
    f.write_text("<h1>hi</h1>")
    from aegis import files

    sent = files.store(beta.app.roots.state_root, f)
    path = files.url(sent["file_id"], sent["name"])
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{alpha.base}/via/beta{path}")
        assert r.status_code == 200 and r.text == "<h1>hi</h1>"
        assert "sandbox" in r.headers["content-security-policy"]
        miss = await c.get(f"{alpha.base}/via/gamma{path}")
        assert miss.status_code == 404
    del s


async def test_aegis_link_add_reads_the_token_from_stdin_and_checks_the_name(
    pair, tmp_path
):
    import sys

    _, beta = pair
    root = tmp_path / "gamma"
    root.mkdir()
    (root / ".aegis.yaml").write_text(CONFIG)

    async def run(*args: str, stdin: str = "") -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "aegis", "link", *args, "--root", str(root),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        out, _ = await p.communicate(stdin.encode())
        return p.returncode, out.decode()

    rc, out = await run(
        "add", "delta", beta.base, "--as", "gamma", stdin=beta.token + "\n"
    )
    assert rc == 1 and "calls itself beta, not delta" in out
    rc, out = await run("add", "beta", beta.base, "--as", "gamma", stdin="wrong\n")
    assert rc == 1 and "refused the token" in out
    rc, out = await run(
        "add", "beta", beta.base, "--as", "gamma", stdin=beta.token + "\n"
    )
    assert rc == 0, out
    rc, out = await run("list")
    assert rc == 0 and "beta" in out and beta.token not in out and "1 links" in out
    path = root / ".aegis" / "state" / "links.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rc, out = await run("remove", "beta")
    assert rc == 0 and json.loads(path.read_text()) == {"links": []}
