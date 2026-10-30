"""Exit through a link: what aegis starts reaches the internet from a linked
server's address (#862, docs/superpowers/specs/2026-10-30-link-exit-design.html).

Two halves. On the near server, ``Exits`` keeps one HTTP proxy on loopback per
link named as an exit; a child gets its URL through the proxy variables
(``Exits.env``), so this covers what honours them and nothing else. For each
stream the proxy opens one websocket to the far server's ``/tunnel``, and
``relay`` there connects to the target. One socket per stream leaves flow
control to TCP, and a download cannot delay the link's own frames.

Only a trusted link can be an exit: the rule that nothing from a linked server
reaches an agent holds for a plain link, and plain HTTP through a relay is
exactly that. A trusted link's token can already run a shell on the far server,
so the relay gives it no new power, and it needs no setting of its own.

Three rules:

- A dead exit fails. The proxy answers 502 with the reason, and nothing falls
  back to the near server's address, the one the person meant to avoid.
- The relay connects to public addresses only, checked after it resolves the
  name, and connects to the address it checked. An agent pushed by a prompt
  injection must not reach the far server's loopback, its private network or a
  cloud's metadata address.
- A stream is bytes both ways. An empty binary frame says this side finished
  writing, so a half-closed connection stays half-closed across the tunnel.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import logging
import secrets
import shlex
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

if TYPE_CHECKING:
    from .links import Links

# The websockets library logs a handshake's headers at DEBUG, the token with them.
_quiet = logging.getLogger(f"{__name__}.tunnel")
_quiet.setLevel(logging.INFO)

CHUNK = 64 * 1024
HEAD_MAX = 64 * 1024
HEAD_TIMEOUT_S = 30.0
# The relay's connect, then the near side's wait for its answer around it.
CONNECT_TIMEOUT_S = 15.0
OPEN_TIMEOUT_S = 20.0
# The first frame on a stream that reached its target.
OPEN = "open"
BAD_TOKEN = 4401
REFUSED = 4403
UNREACHABLE = 4502
# A close frame's reason is at most 123 bytes.
REASON_MAX = 120
USER = "aegis"

TARGET_HEADER = "x-aegis-target"
LINK_HEADER = "x-aegis-link"


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.partition("%")[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """The addresses to try, or ValueError when any answer is not public: a
    name that also resolves to a private address is not one to follow."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses or not all(public_address(a) for a in addresses):
        raise ValueError(f"{host} is not a public address")
    return addresses


async def _reach(
    host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    error: Exception | None = None
    for address in await resolve(host, port):
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError) as e:
            error = e
    raise ConnectionError(f"cannot reach {host}:{port}: {error}")


def split_target(target: str) -> tuple[str, int]:
    """``host:port`` or ``[v6]:port``; ValueError when it is neither."""
    host, _, port = target.rpartition(":")
    number = int(port)
    host = host.removeprefix("[").removesuffix("]")
    if not host or not 0 < number < 65536:
        raise ValueError(f"{target!r} is not host:port")
    return host, number


def _reason(text: str) -> str:
    return text.encode()[:REASON_MAX].decode(errors="ignore")


async def relay(
    ws: WebSocket,
    target: str,
    refusal: Callable[[], str | None],
    count: Callable[[int], None] = lambda _: None,
) -> None:
    """The far half of one stream: ``refusal`` asks whether this socket may
    exit at all. The socket is accepted once the target answers, so the near
    side's handshake and this answer arrive together and cost no round trip."""

    async def refuse(code: int, reason: str) -> None:
        await ws.accept()
        await ws.close(code=code, reason=_reason(reason))

    why = refusal()
    if why is not None:
        await refuse(BAD_TOKEN if why == "bad token" else REFUSED, why)
        return
    try:
        host, port = split_target(target)
        reader, writer = await _reach(host, port)
    except ValueError as e:
        await refuse(REFUSED, str(e))
        return
    except (OSError, UnicodeError) as e:
        await refuse(UNREACHABLE, str(e))
        return

    async def up() -> bool:
        """True when the near side finished writing, False when it left."""
        with contextlib.suppress(OSError):
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return False
                data = message.get("bytes")
                if data:
                    writer.write(data)
                    await writer.drain()
                elif data is not None:
                    if writer.can_write_eof():
                        writer.write_eof()
                    return True
        return False

    async def down() -> None:
        try:
            while data := await reader.read(CHUNK):
                count(len(data))
                await ws.send_bytes(data)
            await ws.send_bytes(b"")
        except OSError:
            # The target dropped the connection: the near side must not read
            # it as a clean end.
            with contextlib.suppress(Exception):
                await ws.close(code=1011)
        except (WebSocketDisconnect, RuntimeError):
            pass

    sending = None
    receiving = None
    try:
        await ws.accept()
        await ws.send_text(OPEN)
        sending = asyncio.create_task(down())
        receiving = asyncio.create_task(up())
        # The stream ends when the near side leaves, or when both have finished.
        if await receiving:
            await sending
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in (sending, receiving):
            if task is not None:
                task.cancel()
        writer.close()
        with contextlib.suppress(Exception):
            await ws.close()


def _git_ssh() -> str:
    """``ssh`` for ``git`` alone: only ``git`` reads this variable, so a bare
    ``ssh`` still goes direct, as the help page says."""
    helper = Path(__file__).with_name("exit_connect.py")
    command = f"{shlex.quote(sys.executable)} -I {shlex.quote(str(helper))} %h %p"
    return f"ssh -o {shlex.quote(f'ProxyCommand={command}')}"


def tunnel_url(url: str) -> str:
    u = urlsplit(url)
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}/tunnel"


class Exit:
    """One link's proxy on loopback."""

    def __init__(self, name: str, links: "Links", refused: Callable[[], str | None]):
        self.name, self.links, self.refused = name, links, refused
        # Another Linux user on this machine must not get to use the exit.
        self.secret = secrets.token_urlsafe(24)
        self._auth = base64.b64encode(f"{USER}:{self.secret}".encode()).decode()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self._sock.setblocking(False)
        self.port = self._sock.getsockname()[1]
        self._server: asyncio.Server | None = None
        self._streams: set[asyncio.Task] = set()
        # A child started now may connect before this runs: the socket already
        # listens, so it waits in the backlog.
        self._starting = asyncio.get_running_loop().create_task(self._start())

    async def _start(self) -> None:
        self._server = await asyncio.start_server(self._serve, sock=self._sock)

    async def close(self) -> None:
        self._starting.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._starting
        if self._server is not None:
            self._server.close()
        else:
            self._sock.close()
        streams = list(self._streams)
        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)

    @property
    def url(self) -> str:
        return f"http://{USER}:{self.secret}@127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        """A child's proxy variables. They replace any the server has: the
        server's own proxy applies to its link, not to the child."""
        out = {
            name: self.url
            for name in (
                "https_proxy",
                "HTTPS_PROXY",
                "http_proxy",
                "HTTP_PROXY",
                "all_proxy",
                "ALL_PROXY",
            )
        }
        # aegis's own MCP and a dev server stay on this machine.
        out["no_proxy"] = out["NO_PROXY"] = "localhost,127.0.0.1,::1"
        out["NODE_USE_ENV_PROXY"] = "1"
        out["GIT_SSH_COMMAND"] = _git_ssh()
        return out

    def playwright_proxy(self) -> dict[str, str]:
        """The same exit as Chromium takes it. It skips loopback on its own."""
        return {
            "server": f"http://127.0.0.1:{self.port}",
            "username": USER,
            "password": self.secret,
        }

    def down(self) -> str | None:
        """Why this exit carries nothing now, or None."""
        why = self.refused()
        if why is not None:
            return why
        link = self.links.outbound(self.name)
        if link is None:
            return "no such link"
        if not link.trusted:
            return "the link is not trusted"
        if link.state != "linked":
            since = time.strftime("%H:%M", time.localtime(link.since))
            return f"link {link.state} since {since}"
        if not link.granted:
            return link.trust_error or "the far server did not grant trust"
        return None

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._streams.add(task)
        try:
            await self._stream(reader, writer)
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            self._streams.discard(task)
            writer.close()

    async def _answer(
        self, writer: asyncio.StreamWriter, status: str, text: str
    ) -> None:
        body = f"exit {self.name}: {text}\n".encode()
        extra = (
            'Proxy-Authenticate: Basic realm="aegis"\r\n'
            if status.startswith("407")
            else ""
        )
        writer.write(
            f"HTTP/1.1 {status}\r\n{extra}Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _stream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        if len(head) > HEAD_MAX:
            await self._answer(
                writer, "431 Request Header Fields Too Large", "too long"
            )
            return
        lines = head.decode("latin-1").split("\r\n")
        request = lines[0].split(" ")
        headers = [line for line in lines[1:] if line]
        if len(request) != 3 or not self._authorized(headers):
            await self._answer(
                writer, "407 Proxy Authentication Required", "bad secret"
            )
            return
        method, where, version = request
        first = b""
        try:
            if method == "CONNECT":
                target = where
                split_target(target)
            else:
                target, first = _forwarded(method, where, version, headers)
        except ValueError:
            await self._answer(writer, "400 Bad Request", f"cannot read {where!r}")
            return
        why = self.down()
        if why is not None:
            await self._answer(writer, "502 Bad Gateway", why)
            return
        try:
            ws = await self._open(target)
        except _Failed as e:
            await self._answer(writer, "502 Bad Gateway", str(e))
            return
        try:
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                await ws.send(first)
            await _pump(reader, writer, ws)
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    def _authorized(self, headers: list[str]) -> bool:
        for line in headers:
            name, _, value = line.partition(":")
            if name.strip().lower() == "proxy-authorization":
                scheme, _, given = value.strip().partition(" ")
                return scheme.lower() == "basic" and hmac.compare_digest(
                    given.strip().encode(), self._auth.encode()
                )
        return False

    async def _open(self, target: str) -> ClientConnection:
        link = self.links.outbound(self.name)
        if link is None:
            raise _Failed("no such link")
        try:
            ws = await connect(
                tunnel_url(link.url),
                additional_headers={
                    "Authorization": f"Bearer {link.token}",
                    LINK_HEADER: link.own,
                    TARGET_HEADER: target,
                },
                open_timeout=OPEN_TIMEOUT_S,
                max_size=None,
                # Encrypted bytes do not compress.
                compression=None,
                ping_interval=20,
                logger=_quiet,
            )
        except InvalidStatus as e:
            raise _Failed(
                f"the far server has no exit (HTTP {e.response.status_code}): "
                "it may need a newer aegis"
            ) from e
        except (OSError, TimeoutError, InvalidHandshake, ValueError) as e:
            raise _Failed(f"cannot reach the far server: {e}") from e
        try:
            first = await asyncio.wait_for(ws.recv(), OPEN_TIMEOUT_S)
        except ConnectionClosed as e:
            raise _Failed(e.rcvd.reason if e.rcvd else "the far server closed") from e
        except TimeoutError as e:
            await ws.close()
            raise _Failed("the far server did not answer") from e
        if first != OPEN:
            await ws.close()
            raise _Failed("the far server answered something else")
        return ws


class _Failed(Exception):
    pass


def _forwarded(
    method: str, where: str, version: str, headers: list[str]
) -> tuple[str, bytes]:
    """A plain HTTP request as the target should read it: in origin form,
    without what was for the proxy, and closing after one answer, since a next
    request on this connection could name another host."""
    u = urlsplit(where)
    if u.scheme != "http" or not u.hostname:
        raise ValueError(where)
    target = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    path = u.path or "/"
    if u.query:
        path += f"?{u.query}"
    kept = [
        line
        for line in headers
        if line.partition(":")[0].strip().lower()
        not in ("proxy-authorization", "proxy-connection", "connection")
    ]
    head = [f"{method} {path} {version}", *kept, "Connection: close", "", ""]
    return f"{target}:{u.port or 80}", "\r\n".join(head).encode("latin-1")


async def _pump(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ws: ClientConnection
) -> None:
    async def up() -> None:
        try:
            while data := await reader.read(CHUNK):
                await ws.send(data)
            await ws.send(b"")
        except (OSError, ConnectionClosed):
            await ws.close()

    sending = asyncio.create_task(up())
    try:
        with contextlib.suppress(ConnectionClosed):
            async for message in ws:
                if message:
                    writer.write(
                        message if isinstance(message, bytes) else message.encode()
                    )
                    await writer.drain()
                elif writer.can_write_eof():
                    writer.write_eof()
    finally:
        sending.cancel()


class Exits:
    """The exits this server has started, by link name; a listener opens the
    first time a child asks for it and stays until the server stops."""

    def __init__(
        self, links: "Links", refused: Callable[[], str | None] = lambda: None
    ):
        self.links, self.refused = links, refused
        self._exits: dict[str, Exit] = {}

    def get(self, name: str) -> Exit:
        found = self._exits.get(name)
        if found is None:
            found = self._exits[name] = Exit(name, self.links, self.refused)
        return found

    def env(self, name: str | None) -> dict[str, str]:
        return {} if name is None else self.get(name).env()

    async def shutdown(self) -> None:
        exits, self._exits = self._exits, {}
        await asyncio.gather(
            *(e.close() for e in exits.values()), return_exceptions=True
        )
