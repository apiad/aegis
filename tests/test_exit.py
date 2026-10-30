"""Exit through a trusted link (#862, exit.py): a child's proxy on the near
server, one websocket per stream, and a relay on the far server. Alpha exits
through beta, two real servers in this process as in test_links.py.
"""

import asyncio
import base64
import logging
import os
import shlex
import socket
import sys
from pathlib import Path

import httpx
import pytest

from aegis import doctor, launch
from aegis import exit as exit_
from aegis.browsers import launch_proxy
from aegis.config import Snapshot, write
from aegis.links import LinkStore
from aegis.net.config import NetConfig
from aegis.net.probe import Reach, Trace
from aegis.net.service import Probes
from aegis.ops import OpError
from aegis.roots import make_roots

from .conftest import until
from .test_links import Node
from .test_trusted_links import _granted, trusted  # noqa: F401


class Target:
    """A TCP server standing in for the internet: answers each request."""

    def __init__(self, body: bytes = b"hello from the target"):
        self.body, self.hits, self.open = body, 0, 0

    async def __aenter__(self) -> "Target":
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self.server.close()

    async def _serve(self, reader, writer) -> None:
        self.hits += 1
        self.open += 1
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.head = head.decode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                % len(self.body)
                + self.body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            self.open -= 1
            writer.close()


@pytest.fixture
def anywhere(monkeypatch):
    """The target above is on loopback, which the relay refuses."""
    monkeypatch.setattr(exit_, "public_address", lambda _: True)


async def _connect(port: int, secret: str, target: str, auth: bool = True):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    token = base64.b64encode(f"{exit_.USER}:{secret}".encode()).decode()
    line = f"Proxy-Authorization: Basic {token}\r\n" if auth else ""
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{line}\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
    return reader, writer, head.decode()


async def _body(reader, head: str) -> str:
    n = int(head.lower().split("content-length: ")[1].split("\r\n")[0])
    return (await reader.readexactly(n)).decode()


async def test_connect_reaches_the_target_through_the_far_server(trusted, anywhere):  # noqa: F811
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        assert head.startswith("HTTP/1.1 200")
        w.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await w.drain()
        answer = await asyncio.wait_for(r.read(), 10)
        w.close()
    assert b"hello from the target" in answer
    assert t.hits == 1
    assert beta.app.exit_bytes["alpha"] >= len(answer)


async def test_a_plain_request_is_forwarded_in_origin_form(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        async with httpx.AsyncClient(proxy=out.url, trust_env=False) as c:
            r = await c.get(f"http://127.0.0.1:{t.port}/a/b?c=1", timeout=10)
    assert r.text == "hello from the target"
    first = t.head.split("\r\n")[0]
    assert first == "GET /a/b?c=1 HTTP/1.1"
    assert "proxy-authorization" not in t.head.lower()
    assert "connection: close" in t.head.lower()


async def test_the_child_env_names_the_exit_and_keeps_loopback_direct(trusted):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    env = alpha.app.exits.env("beta")
    assert env["HTTPS_PROXY"] == env["https_proxy"] == out.url
    assert env["ALL_PROXY"] == out.url
    assert "127.0.0.1" in env["NO_PROXY"] and "::1" in env["no_proxy"]
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert alpha.app.exits.env(None) == {}
    # One listener per link, however many children ask.
    assert alpha.app.exits.get("beta") is out


async def test_the_wrong_secret_gets_407(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        for secret, auth in (("nope", True), (out.secret, False)):
            r, w, head = await _connect(out.port, secret, f"127.0.0.1:{t.port}", auth)
            assert head.startswith("HTTP/1.1 407")
            assert "Proxy-Authenticate: Basic" in head
            w.close()
        assert t.hits == 0


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_public_address_refuses_what_is_not_the_internet(address):
    assert not exit_.public_address(address)


@pytest.mark.parametrize("address", ["1.1.1.1", "2606:4700:4700::1111"])
def test_public_address_takes_the_internet(address):
    assert exit_.public_address(address)


async def test_the_relay_refuses_a_private_target(trusted):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        body = await _body(r, head)
        w.close()
        assert head.startswith("HTTP/1.1 502")
        assert "not a public address" in body
        assert t.hits == 0


async def test_a_name_with_any_private_answer_is_refused(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake(host, port, **_):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake)
    with pytest.raises(ValueError, match="not a public address"):
        await exit_.resolve("rebound.example", 443)


async def test_a_plain_link_is_no_exit(tmp_path, fake_claude, anywhere):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(
            lambda: alpha.app.links.get("beta").state == "linked",
            timeout=10,
            what="the link",
        )
        out = alpha.app.exits.get("beta")
        async with Target() as t:
            r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
            body = await _body(r, head)
            w.close()
            assert head.startswith("HTTP/1.1 502")
            assert "not trusted" in body
            assert t.hits == 0
    finally:
        await alpha.stop()
        await beta.stop()


async def test_a_member_token_on_the_far_server_ends_the_exit(trusted, anywhere):  # noqa: F811
    """Checked by the relay itself at each stream, whatever the near side
    still believes about the trust it was granted."""
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        beta.app.principals.has_members = lambda: True
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        body = await _body(r, head)
        w.close()
        assert head.startswith("HTTP/1.1 502")
        assert "besides its admin" in body
        assert t.hits == 0


async def test_a_wrong_token_is_refused_by_the_relay(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    alpha.app.links.outbound("beta").token = "not-the-token"
    async with Target() as t:
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        await _body(r, head)
        w.close()
        assert head.startswith("HTTP/1.1 502")
        assert t.hits == 0


async def test_a_browser_cannot_open_a_stream(trusted):  # noqa: F811
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    _, beta = trusted
    with pytest.raises(InvalidStatus):
        await connect(
            f"ws://127.0.0.1:{beta.port}/tunnel",
            origin=beta.base,
            additional_headers={
                "Authorization": f"Bearer {beta.token}",
                exit_.LINK_HEADER: "alpha",
                exit_.TARGET_HEADER: "1.1.1.1:443",
            },
        )


async def test_a_dead_link_answers_502_and_never_goes_direct(trusted, anywhere):  # noqa: F811
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")
    await beta.stop()
    await until(
        lambda: alpha.app.links.get("beta").state != "linked",
        timeout=10,
        what="the link down",
    )
    async with Target() as t:
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        body = await _body(r, head)
        w.close()
        assert head.startswith("HTTP/1.1 502")
        assert body.startswith("exit beta: link ") and "since" in body
        assert t.hits == 0
    await beta.start()


async def test_an_unknown_link_answers_502(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("nowhere")
    r, w, head = await _connect(out.port, out.secret, "1.1.1.1:443")
    assert head.startswith("HTTP/1.1 502")
    assert "no such link" in await _body(r, head)
    w.close()


async def test_a_half_closed_stream_still_gets_its_answer(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")

    async def echo_after_eof(reader, writer):
        data = await reader.read()
        writer.write(data[::-1])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo_after_eof, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{port}")
    assert head.startswith("HTTP/1.1 200")
    w.write(b"abc")
    await w.drain()
    w.write_eof()
    assert await asyncio.wait_for(r.read(), 10) == b"cba"
    w.close()
    server.close()


async def test_leaving_mid_transfer_closes_the_far_connection(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    closed = asyncio.Event()

    async def endless(reader, writer):
        try:
            while True:
                writer.write(b"x" * 65536)
                await writer.drain()
        except OSError:
            pass
        finally:
            closed.set()
            writer.close()

    server = await asyncio.start_server(endless, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{port}")
    await r.readexactly(1000)
    w.close()
    await asyncio.wait_for(closed.wait(), 10)
    server.close()


async def test_a_slow_reader_slows_the_far_read(trusted, anywhere):  # noqa: F811
    """Flow control holds across the tunnel: with nobody reading, the target
    stops being read far short of what it could send."""
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    sent = 0
    total = 200 * 1024 * 1024

    async def fast(reader, writer):
        nonlocal sent
        try:
            while sent < total:
                writer.write(b"x" * 65536)
                await writer.drain()
                sent += 65536
        except OSError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(fast, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{port}")
    assert head.startswith("HTTP/1.1 200")
    await asyncio.sleep(1.5)
    assert sent < total / 2
    w.close()
    server.close()


async def test_several_streams_at_once(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")

    async def one(t: Target) -> str:
        async with httpx.AsyncClient(proxy=out.url, trust_env=False) as c:
            return (await c.get(f"http://127.0.0.1:{t.port}/", timeout=15)).text

    async with Target() as t:
        got = await asyncio.gather(*(one(t) for _ in range(8)))
    assert got == ["hello from the target"] * 8
    assert t.hits == 8


async def test_stopping_the_server_closes_its_listener(trusted):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    r, w = await asyncio.open_connection("127.0.0.1", out.port)
    w.close()
    await alpha.app.exits.shutdown()
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", out.port)


async def test_no_secret_or_token_is_logged(trusted, anywhere, caplog):  # noqa: F811
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")
    with caplog.at_level(logging.DEBUG):
        async with Target() as t:
            r, w, _ = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
            w.close()
    assert out.secret not in caplog.text
    assert beta.token not in caplog.text


# -- using it: configuration and what a process gets --------------------------------
def _read_environ(pid: int) -> dict[str, str]:
    raw = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
    return dict(e.decode().split("=", 1) for e in raw if b"=" in e)


async def _environ(pid: int) -> dict[str, str]:
    """A process in the middle of ``exec`` reads as an empty environment: the
    fake claude is a script that execs Python, and a read right after the spawn
    lands there in most runs. Every child has PATH, so it marks a real read."""
    await until(lambda: "PATH" in _read_environ(pid), timeout=10, what="its environ")
    return _read_environ(pid)


CONFIG_EXIT = """\
default_agent: opus
exit_via: beta
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  direct: {harness: claude-code, model: opus, effort: high, permission: full, exit: "off"}
queues:
  general: {agent: opus, max_parallel: 1}
"""


async def _reload(node: Node, text: str) -> None:
    (node.root / ".aegis.yaml").write_text(text)
    await until(
        lambda: (
            node.app.config.current().doc.exit_via
            == ("beta" if "exit_via" in text else None)
        ),
        timeout=5,
        what="the config",
    )


async def test_a_session_exits_and_a_profile_with_exit_off_does_not(trusted):  # noqa: F811
    alpha, _ = trusted
    await _reload(alpha, CONFIG_EXIT)
    url = alpha.app.exits.get("beta").url
    s = await alpha.spawn(agent="opus")
    await until(lambda: s.pid, timeout=10, what="the process")
    env = await _environ(s.pid)
    assert env["HTTPS_PROXY"] == url and "127.0.0.1" in env["NO_PROXY"]
    assert s.wire()["exit"] == "beta"
    d = await alpha.spawn(agent="direct")
    await until(lambda: d.pid, timeout=10, what="the process")
    assert (await _environ(d.pid)).get("HTTPS_PROXY") != url
    assert d.wire()["exit"] is None


async def test_a_profile_alone_can_name_the_exit(trusted):  # noqa: F811
    alpha, _ = trusted
    text = CONFIG_EXIT.replace("exit_via: beta\n", "").replace(
        'exit: "off"', "exit: beta"
    )
    await _reload(alpha, text)
    await until(
        lambda: launch.resolve_via("direct") == "beta", timeout=5, what="the profile"
    )
    assert launch.resolve_via("opus") is None
    assert launch.resolve_via() is None


async def test_a_monitor_and_a_workflow_step_get_the_exit(trusted):  # noqa: F811
    alpha, _ = trusted
    await _reload(alpha, CONFIG_EXIT)
    assert launch.resolve_via() == "beta"
    p = await launch.child(
        ["sh", "-c", "echo $HTTPS_PROXY"],
        stdout=launch.PIPE,
        user=None,
        via=launch.resolve_via(),
    )
    out, _ = await p.communicate()
    assert out.decode().strip() == alpha.app.exits.get("beta").url


async def test_a_direct_child_keeps_the_servers_environment(trusted, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HTTPS_PROXY", "http://corp.example:3128")
    p = await launch.child(
        ["sh", "-c", "echo $HTTPS_PROXY"], stdout=launch.PIPE, user=None, via=None
    )
    out, _ = await p.communicate()
    assert out.decode().strip() == "http://corp.example:3128"


async def test_link_exit_writes_the_config_and_refuses_an_unknown_link(trusted):  # noqa: F811
    alpha, _ = trusted
    r = await alpha.app.registry.call("link.exit", {"name": "beta"})
    assert r == {"exit_via": "beta"}
    assert "exit_via: beta" in (alpha.root / ".aegis.yaml").read_text()
    await until(lambda: launch.resolve_via() == "beta", timeout=5, what="the config")
    await alpha.app.registry.call("link.exit", {"name": None})
    assert "exit_via" not in (alpha.root / ".aegis.yaml").read_text()
    with pytest.raises(OpError):
        await alpha.app.registry.call("link.exit", {"name": "nowhere"})


async def test_a_settings_save_keeps_the_exit_keys(trusted):  # noqa: F811
    alpha, _ = trusted
    await _reload(alpha, CONFIG_EXIT)
    snap = alpha.app.config.current()
    assert write(alpha.app.config.path, snap.doc, snap.stamp) == []
    text = (alpha.root / ".aegis.yaml").read_text()
    assert "exit_via: beta" in text and "exit: off" in text.replace('"', "")


async def test_the_far_server_shows_the_bytes_each_link_took(trusted, anywhere):  # noqa: F811
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")
    async with Target() as t:
        async with httpx.AsyncClient(proxy=out.url, trust_env=False) as c:
            await c.get(f"http://127.0.0.1:{t.port}/", timeout=10)
    (row,) = await beta.app.registry.call("link.incoming", {})
    assert row["name"] == "alpha" and row["exit_bytes"] > 0


async def test_the_via_reading_says_the_address_and_the_link_going_down(trusted):  # noqa: F811
    alpha, beta = trusted
    await _reload(alpha, CONFIG_EXIT)
    seen = []

    async def trace(timeout, proxy=None):
        seen.append(proxy)
        return Trace(ok=True, ip="203.0.113.9", colo="HEL", loc="FI")

    async def reach(anchors, timeout):
        return Reach(ok=True, rtt_ms=1.0, anchor="1.1.1.1:443")

    alpha.app.net._probes = Probes(reach=reach, trace=trace, throughput=None)
    alpha.app.net._cfg = lambda: NetConfig(speed_interval=0)
    await alpha.app.net.refresh()
    via = alpha.app.net.wire()["via"]
    assert via["name"] == "beta" and via["ip"] == "203.0.113.9" and via["down"] == ""
    assert alpha.app.exits.get("beta").url in seen
    await beta.stop()
    await until(
        lambda: alpha.app.links.get("beta").state != "linked",
        timeout=10,
        what="the link down",
    )
    await alpha.app.net.refresh()
    assert "since" in alpha.app.net.wire()["via"]["down"]
    await beta.start()


async def test_a_multi_user_server_refuses_an_exit(trusted, anywhere):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    out.refused = lambda: "an exit is off on a multi-user server"
    async with Target() as t:
        r, w, head = await _connect(out.port, out.secret, f"127.0.0.1:{t.port}")
        assert head.startswith("HTTP/1.1 502")
        assert "multi-user" in await _body(r, head)
        w.close()
        assert t.hits == 0


def test_doctor_flags_a_name_that_is_no_trusted_link(tmp_path):
    roots = make_roots(tmp_path, None)
    roots.state_root.mkdir(parents=True, exist_ok=True)
    LinkStore(roots.state_root / "links.json").save(
        [{"name": "plain", "url": "http://x", "token": "t", "trusted": False},
         {"name": "good", "url": "http://y", "token": "t", "trusted": True}]
    )  # fmt: skip

    def finding(name):
        snap = Snapshot(path=tmp_path / "x", exit_via=name)
        return doctor._exit(snap, roots, multi_user=False)

    assert doctor._exit(Snapshot(path=tmp_path / "x"), roots, multi_user=False) == []
    assert finding("good")[0].level == "ok"
    for name in ("plain", "gone"):
        assert finding(name)[0].level == "error"
    snap = Snapshot(path=tmp_path / "x", exit_via="good")
    assert doctor._exit(snap, roots, multi_user=True)[0].level == "error"


# -- the gaps: git over SSH and Chromium ---------------------------------------------
async def _through_helper(url: str, port: int, send: bytes) -> tuple[bytes, bytes, int]:
    helper = Path(exit_.__file__).with_name("exit_connect.py")
    p = await launch.child(
        [sys.executable, "-I", str(helper), "127.0.0.1", str(port)],
        env={"HTTPS_PROXY": url, "PATH": os.environ["PATH"]},
        stdin=launch.PIPE,
        stdout=launch.PIPE,
        stderr=launch.PIPE,
        user=None,
        via=None,
    )
    out, err = await asyncio.wait_for(p.communicate(send), 15)
    return out, err, p.returncode


async def test_the_ssh_helper_carries_a_stream_through_the_exit(trusted, anywhere):  # noqa: F811
    alpha, beta = trusted
    out = alpha.app.exits.get("beta")

    async def reverse(reader, writer):
        data = await reader.read()
        writer.write(data[::-1])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(reverse, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    got, _, code = await _through_helper(out.url, port, b"abc")
    server.close()
    assert (got, code) == (b"cba", 0)
    assert beta.app.exit_bytes["alpha"] >= 3


async def test_the_ssh_helper_says_why_when_the_exit_refuses(trusted):  # noqa: F811
    alpha, _ = trusted
    out = alpha.app.exits.get("beta")
    got, err, code = await _through_helper(out.url, 22, b"")
    assert got == b"" and code == 1
    assert b"not a public address" in err


async def test_git_gets_the_helper_and_a_persons_own_command_stays(trusted):  # noqa: F811
    alpha, _ = trusted
    command = alpha.app.exits.env("beta")["GIT_SSH_COMMAND"]
    words = shlex.split(command)
    assert words[:2] == ["ssh", "-o"]
    assert words[2].startswith("ProxyCommand=") and words[2].endswith(
        "exit_connect.py %h %p"
    )
    assert launch._via("beta", {"A": "1"})["GIT_SSH_COMMAND"] == command
    kept = launch._via("beta", {"GIT_SSH_COMMAND": "ssh -i mine"})
    assert kept["GIT_SSH_COMMAND"] == "ssh -i mine"
    assert kept["HTTPS_PROXY"] == alpha.app.exits.get("beta").url


async def test_chromium_gets_the_exit_only_when_one_is_in_force(trusted):  # noqa: F811
    alpha, _ = trusted
    assert launch_proxy() == {}
    await _reload(alpha, CONFIG_EXIT)
    out = alpha.app.exits.get("beta")
    assert launch_proxy() == {
        "proxy": {
            "server": f"http://127.0.0.1:{out.port}",
            "username": "aegis",
            "password": out.secret,
        }
    }


@pytest.mark.browser
@pytest.mark.slow
async def test_chromium_reaches_a_page_through_the_exit(trusted, monkeypatch):  # noqa: F811
    """A name the near server cannot resolve, which only the relay's answer
    turns into the target: a page that loads came through it."""
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    alpha, _ = trusted
    await _reload(alpha, CONFIG_EXIT)

    async def here(host, port):
        return ["127.0.0.1"]

    monkeypatch.setattr(exit_, "resolve", here)
    async with Target(b"through the exit") as t:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_proxy())
            page = await browser.new_page()
            await page.goto(f"http://only-the-relay.invalid:{t.port}/")
            assert "through the exit" in await page.content()
            await browser.close()
    assert t.hits >= 1


async def test_a_stopped_server_leaves_no_exit_behind(tmp_path, fake_claude):
    """The next App in this process must not inherit it: a child would get a
    proxy whose listener is gone."""
    node = await Node(tmp_path / "a", "a", fake_claude).start()
    assert launch.exits_in_force() is node.app.exits
    await node.stop()
    assert launch.exits_in_force() is None
    assert launch.resolve_via() is None
