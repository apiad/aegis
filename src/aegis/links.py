"""Links: this server as a client of another aegis server.

A link is one websocket this server opens to another, saying ``hello`` with
that server's token, exactly as a browser would, plus ``link: {server, user}``
so the far side records who is acting. Through it the people on this server
see and drive the far server's sessions, and this server's agents hand off to
the far server's agents.

The rule the whole design hangs on (DESIGN.md, "A link is a client"): the far
server can never reach this one. The link sends requests and reads five kinds
of frame back: ``welcome``, ``reply`` to a call it made, ``snapshot`` and
``patch`` for a subscription it opened, and ``error``. It runs no request
handler, so a ``call`` or ``sub`` arriving from the far side is dropped and
logged, and nothing on this server runs because of it.

A trusted link (#684) is the one exception, for two servers one person alone
uses. This server asks for trust in its hello; the far server grants it only
to its admin's token while it has no member tokens, and says so in
``welcome``. Then, and only then, the link also answers ``rcall`` frames with
``rreply``, running the far server's calls here as a person acting through a
link, and a ``trust`` frame from the far side ends that again. The far server
keeps a ``Backlink`` for the socket, so its agents address this server as
``handle@<name>`` over the socket this server opened: a laptop behind NAT is
reached without accepting a connection.

Subscriptions forwarded for browsers carry a link-unique ``sid``, and the far
server keys a link socket's subscriptions by it, so two browsers watching one
channel each get their own numbered patches. None survive a drop: the
``links`` channel tells browsers the link is back and they resubscribe with
the revision they hold, as after their own reconnect.

Links are kept in ``<state>/links.json``, mode 0600, because a link holds the
far server's token. Never in ``.aegis.yaml``: Workspace's is in git, and the
far server may read the same file. The file is the configuration: an edit, or
``aegis link add`` while the server runs, takes effect within a second.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import (
    ConnectionClosed,
    InvalidHandshake,
    InvalidStatus,
    InvalidURI,
)

from .ops import OpError

log = logging.getLogger("aegis.links")

# The client protocol's version, spoken by browsers and links alike.
PROTO = 3

# The release that first speaks links; both ends of one need it.
LINKS_SINCE = "2.4.0"
# The release that first grants trusted links.
TRUSTED_SINCE = "2.17.0"

HELLO_TIMEOUT_S = 10.0
CALL_TIMEOUT_S = 15.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
WATCH_EVERY_S = 1.0
ACCEPTED = ("welcome", "reply", "snapshot", "patch", "error")
# Frames only a link the far server trusted accepts: its calls, and its word
# that the trust ended.
TRUSTED = ("rcall", "trust")
# What a link answers an rcall with, given the far server's name, the op and
# its params: the reply's ``result`` or ``error``.
Answer = Callable[[str, str, Any], Awaitable[dict]]

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class LinkError(Exception):
    """A probe that did not end in a welcome: ``state`` says why."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


def ws_url(url: str) -> str:
    """The far server's socket URL from its page URL."""
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"{url!r} is not an http or https URL")
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}/ws"


def base_url(url: str) -> str:
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.netloc}"


async def _hello(
    url: str, token: str, own: str, user: str, trusted: bool = False
) -> tuple[ClientConnection, dict, float]:
    """Open the socket and say hello; the connection, its welcome and the
    round trip in ms. Raises LinkError for every way it can fail."""
    t0 = time.monotonic()
    try:
        ws = await connect(
            ws_url(url), open_timeout=HELLO_TIMEOUT_S, max_size=None, ping_interval=20
        )
    except InvalidStatus as e:
        # A link sends no Origin, and a server from before links closes any
        # socket whose Origin is not its own page: the handshake fails with a
        # 403 before there is a hello to answer, so its version cannot be read:
        # nothing before the refusal carries one.
        if e.response.status_code == 403:
            raise LinkError(
                "mismatch",
                f"{url} refused the connection (HTTP 403). It most likely "
                f"predates links: both servers need aegis {LINKS_SINCE} or newer",
            ) from e
        raise LinkError("offline", f"cannot reach {url}: {e}") from e
    except (OSError, TimeoutError, InvalidHandshake, InvalidURI, ValueError) as e:
        raise LinkError("offline", f"cannot reach {url}: {e}") from e
    try:
        hello = {
            "t": "hello",
            "token": token,
            "proto": PROTO,
            "link": {"server": own, "user": user}
            | ({"trusted": True} if trusted else {}),
        }
        await ws.send(json.dumps(hello))
        first = json.loads(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_S))
        if first.get("t") == "error":  # the far side speaks another protocol
            raise LinkError("mismatch", str(first.get("message")))
        if first.get("t") != "welcome":
            raise LinkError(
                "offline", f"{url} answered {first.get('t')!r}, not welcome"
            )
        if first.get("proto") != PROTO:
            raise LinkError(
                "mismatch",
                f"{url} speaks protocol {first.get('proto')}, this server {PROTO}",
            )
    except ConnectionClosed as e:
        await ws.close()
        code = e.rcvd.code if e.rcvd else None
        if code == 4401:
            raise LinkError("unauthorized", f"{url} refused the token") from e
        if code == 4400:
            raise LinkError("mismatch", f"{url} speaks another protocol") from e
        raise LinkError("offline", f"{url} closed the socket ({code})") from e
    except (TimeoutError, ValueError) as e:
        await ws.close()
        raise LinkError("offline", f"{url} said no welcome: {e}") from e
    except LinkError:
        await ws.close()
        raise
    return ws, first, (time.monotonic() - t0) * 1000


async def probe(
    url: str, token: str, own: str, user: str, trusted: bool = False
) -> dict:
    """Connect once and return the far server's welcome; LinkError otherwise,
    and a ``trust_refused`` one when trust was asked and not granted."""
    ws, welcome, _ = await _hello(url, token, own, user, trusted)
    await ws.close()
    if trusted and welcome.get("trusted") is not True:
        raise LinkError("trust_refused", trust_refusal(welcome))
    return welcome


def trust_refusal(welcome: dict) -> str:
    """Why a welcome did not grant the trust a link asked for."""
    why = welcome.get("trust_refused")
    if isinstance(why, str) and why:
        return why
    return (
        f"{welcome.get('server')} does not grant trusted links; "
        f"both servers need aegis {TRUSTED_SINCE} or newer"
    )


class LinkStore:
    """``links.json``: written by write-then-rename with mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("%s does not parse, keeping no links: %s", self.path, e)
            return []
        out = []
        for e in data.get("links", []) if isinstance(data, dict) else []:
            if isinstance(e, dict) and all(
                isinstance(e.get(k), str) for k in ("name", "url", "token")
            ):
                out.append(e)
        return out

    def save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"links": entries}, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


Send = Callable[[dict], None]


class Link:
    """One outbound link, reconnecting with backoff until stopped."""

    def __init__(
        self,
        name: str,
        url: str,
        token: str,
        own: str,
        user: str,
        on_change: Callable[[], None] = lambda: None,
        trusted: bool = False,
        answer: Answer | None = None,
    ) -> None:
        self.name, self.url, self.token = name, base_url(url), token
        self.own, self.user = own, user
        self.on_change = on_change
        # Asked for in links.json; granted by the far server's welcome, for
        # this socket only, and ended by its trust frame.
        self.trusted, self.answer = trusted, answer
        self.granted = False
        self.trust_error = ""
        self._answering: set[asyncio.Task] = set()
        self.state = "connecting"
        self.since = time.time()
        self.rtt_ms: int | None = None
        self.remote: dict = {}
        self.error = ""
        self.attempts = 0
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subs: dict[str, Send] = {}
        self._watches: dict[str, str] = {}
        self._out: asyncio.Queue[dict] = asyncio.Queue()
        self._dropped: set[str] = set()

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        ws = self._ws
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        # Cancelling the reader leaves the socket open until it is collected;
        # the far server must see it close, to drop a way back (Backlink).
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _set(self, state: str, error: str = "") -> None:
        if state != self.state or error != self.error:
            self.state, self.error, self.since = state, error, time.time()
            self.on_change()

    async def _run(self) -> None:
        backoff = BACKOFF_MIN_S
        while True:
            self.attempts += 1
            try:
                ws, welcome, rtt = await _hello(
                    self.url, self.token, self.own, self.user, self.trusted
                )
            except LinkError as e:
                self._set(e.state, e.message)
                if e.state == "unauthorized":
                    return  # a new token is a new link; retrying cannot help
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
                continue
            if welcome.get("server") != self.name:
                await ws.close()
                self._set(
                    "mismatch",
                    f"this server calls itself {welcome.get('server')}, "
                    f"the link expects {self.name}",
                )
                await asyncio.sleep(BACKOFF_MAX_S)
                continue
            self._ws, self.remote, self.rtt_ms = ws, welcome, round(rtt)
            self.granted = self.trusted and welcome.get("trusted") is True
            self.trust_error = (
                trust_refusal(welcome) if self.trusted and not self.granted else ""
            )
            backoff = BACKOFF_MIN_S
            self._set("linked")
            self.on_change()  # the trust may differ from the last socket's
            for sid, log_id in self._watches.items():
                self._out.put_nowait(
                    {"t": "watch", "sid": sid, "session": log_id, "visible": True}
                )
            writer = asyncio.get_running_loop().create_task(self._write(ws))
            try:
                async for raw in ws:
                    self._receive(raw)
                    if ws.latency:
                        self.rtt_ms = round(ws.latency * 1000)
            except ConnectionClosed:
                pass
            finally:
                writer.cancel()
                self._ws = None
                self._lost()
            self._set("offline", f"{self.url} closed the link")
            await asyncio.sleep(backoff)

    async def _write(self, ws: ClientConnection) -> None:
        while True:
            msg = await self._out.get()
            await ws.send(json.dumps(msg, ensure_ascii=False))

    def _lost(self) -> None:
        """The socket went: every call fails, every subscription ends."""
        self.granted = False
        for task in self._answering:
            task.cancel()
        self._answering.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(
                    {
                        "error": {
                            "code": "server_offline",
                            "message": f"{self.name} went offline",
                        }
                    }
                )
        self._pending.clear()
        for sid, send in list(self._subs.items()):
            send(
                {
                    "t": "error",
                    "sid": sid,
                    "error": {
                        "code": "server_offline",
                        "message": f"{self.name} went offline",
                    },
                }
            )
        self._subs.clear()
        self._out = asyncio.Queue()

    # -- what comes back -------------------------------------------------------
    def _receive(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            msg = {}
        t = msg.get("t") if isinstance(msg, dict) else None
        if t in TRUSTED and self.granted:
            self._trusted_frame(msg)
            return
        if t not in ACCEPTED:
            kind = t if isinstance(t, str) else "malformed"
            if kind not in self._dropped:
                self._dropped.add(kind)
                log.warning("dropped a %s frame from linked server %s", kind, self.name)
            return
        if t == "reply":
            n = msg.get("id")
            fut = self._pending.pop(n, None) if isinstance(n, int) else None
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if t == "welcome":
            return
        sid = msg.get("sid")
        send = self._subs.get(sid) if isinstance(sid, str) else None
        if send is not None:
            send(msg)
        elif t == "error":
            log.info(
                "linked server %s: %s",
                self.name,
                msg.get("message") or msg.get("error"),
            )

    def _trusted_frame(self, msg: dict) -> None:
        """A call from the far server, or its word that the trust ended."""
        if msg["t"] == "trust":
            if msg.get("trusted") is not True:
                self.granted = False
                why = msg.get("reason")
                self.trust_error = why if isinstance(why, str) else "trust ended"
                log.warning("linked server %s ended the trust: %s", self.name, why)
                self.on_change()
            return
        task = asyncio.get_running_loop().create_task(self._rcall(msg))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)

    async def _rcall(self, msg: dict) -> None:
        reply: dict = {"t": "rreply", "id": msg.get("id")}
        op = msg.get("op")
        if self.answer is None or not isinstance(op, str):
            reply["error"] = {"code": "bad_call", "message": "no such call"}
        else:
            reply.update(await self.answer(self.name, op, msg.get("params")))
        if self.granted:  # an answer must not outlive the trust it ran under
            self._out.put_nowait(reply)

    # -- what goes out ---------------------------------------------------------
    def describe(self) -> str:
        ago = int(time.time() - self.since)
        return f"{self.name} is {self.state} since {ago} s ago" + (
            f": {self.error}" if self.error else ""
        )

    async def call_raw(
        self,
        op: str,
        params: Any,
        server: str | None = None,
        timeout: float = CALL_TIMEOUT_S,
    ) -> dict:
        """The far server's reply to one call: ``{"result": …}`` or ``{"error": …}``."""
        if self.state != "linked" or self._ws is None:
            return {"error": {"code": "server_offline", "message": self.describe()}}
        n = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        msg: dict = {"t": "call", "id": n, "op": op, "params": params or {}}
        if server is not None:
            msg["server"] = server
        self._out.put_nowait(msg)
        try:
            reply = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self._pending.pop(n, None)
            return {
                "error": {
                    "code": "timeout",
                    "message": f"{self.name} did not answer {op} within {timeout:g} s",
                }
            }
        return {k: reply[k] for k in ("result", "error") if k in reply}

    async def call(
        self, op: str, params: Any = None, timeout: float = CALL_TIMEOUT_S
    ) -> Any:
        reply = await self.call_raw(op, params, timeout=timeout)
        if "error" in reply:
            e = reply["error"] or {}
            raise OpError(str(e.get("code", "error")), str(e.get("message", "")))
        return reply.get("result")

    def sub(
        self, sid: str, channel: str, since: int | None, send: Send, merge: bool = False
    ) -> None:
        if self.state != "linked":
            send(
                {
                    "t": "error",
                    "sid": sid,
                    "error": {"code": "server_offline", "message": self.describe()},
                }
            )
            return
        self._subs[sid] = send
        msg: dict = {"t": "sub", "sid": sid, "channel": channel}
        if since is not None:
            msg["since"] = since
        if (
            merge
        ):  # the browser's ask, passed on: a far server too old to know it ignores it
            msg["merge"] = True
        self._out.put_nowait(msg)

    def watch(self, sid: str, log_id: str | None) -> None:
        """Tell the far server whether a browser here shows one of its sessions
        in a visible page. The link keeps every watch and sends them again when
        it comes back, because the far server forgot them when the socket went."""
        if log_id is None:
            self._watches.pop(sid, None)
        else:
            self._watches[sid] = log_id
        if self.state != "linked":
            return
        self._out.put_nowait(
            {"t": "watch", "sid": sid, "session": log_id, "visible": log_id is not None}
        )

    def unsub(self, sid: str) -> None:
        if self._subs.pop(sid, None) is not None and self.state == "linked":
            self._out.put_nowait({"t": "unsub", "sid": sid})

    def drop(self, prefix: str) -> None:
        """End every subscription a browser opened, when its socket closes."""
        for sid in [s for s in self._subs if s.startswith(prefix)]:
            self.unsub(sid)

    def wire(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "state": self.state,
            "since": self.since,
            "rtt_ms": self.rtt_ms,
            "proto": self.remote.get("proto"),
            "error": self.error,
            "trusted": self.trusted,
            "granted": self.granted,
            "trust_error": self.trust_error,
        }


class Backlink:
    """The way back to a server that linked this one with trust: its calls go
    down the socket that server opened, as ``rcall``, and come back as
    ``rreply``. It has the face of a ``Link`` for calls, so agents and the
    router find it by name; subscriptions are refused, since live views stay
    one way. ``check`` says why the trust no longer holds, or None, and is
    asked before every call: a member token minted here ends it at once."""

    url = None  # nothing to fetch files from: the far server has no route here

    def __init__(
        self,
        name: str,
        send: Send,
        check: Callable[[], str | None],
        on_end: Callable[[Backlink], None] = lambda _: None,
    ) -> None:
        self.name = name
        self.state = "linked"
        self.since = time.time()
        self.rtt_ms: int | None = None
        self.remote: dict = {}
        self.error = ""
        self._send, self._check, self._on_end = send, check, on_end
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    def describe(self) -> str:
        return f"{self.name} is {self.state}" + (
            f": {self.error}" if self.error else ""
        )

    def end(self, reason: str) -> None:
        """The trust no longer holds: tell the far server, fail every call."""
        if self.state != "linked":
            return
        self.state, self.error, self.since = "ended", reason, time.time()
        self._send({"t": "trust", "trusted": False, "reason": reason})
        self._fail("trust_ended", f"the trust with {self.name} ended: {reason}")
        self._on_end(self)

    def lost(self) -> None:
        """The socket went."""
        if self.state == "linked":
            self.state, self.since = "offline", time.time()
        self._fail("server_offline", f"{self.name} went offline")

    def _fail(self, code: str, message: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"error": {"code": code, "message": message}})
        self._pending.clear()

    def resolve(self, msg: dict) -> None:
        n = msg.get("id")
        fut = self._pending.pop(n, None) if isinstance(n, int) else None
        if fut is not None and not fut.done():
            fut.set_result(msg)

    async def call_raw(
        self,
        op: str,
        params: Any,
        server: str | None = None,
        timeout: float = CALL_TIMEOUT_S,
    ) -> dict:
        if self.state == "linked" and (why := self._check()) is not None:
            self.end(why)
        if self.state != "linked":
            code = "trust_ended" if self.state == "ended" else "server_offline"
            return {"error": {"code": code, "message": self.describe()}}
        n = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        self._send({"t": "rcall", "id": n, "op": op, "params": params or {}})
        try:
            reply = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self._pending.pop(n, None)
            return {
                "error": {
                    "code": "timeout",
                    "message": f"{self.name} did not answer {op} within {timeout:g} s",
                }
            }
        return {k: reply[k] for k in ("result", "error") if k in reply}

    async def call(
        self, op: str, params: Any = None, timeout: float = CALL_TIMEOUT_S
    ) -> Any:
        reply = await self.call_raw(op, params, timeout=timeout)
        if "error" in reply:
            e = reply["error"] or {}
            raise OpError(str(e.get("code", "error")), str(e.get("message", "")))
        return reply.get("result")

    def sub(
        self, sid: str, channel: str, since: int | None, send: Send, merge: bool = False
    ) -> None:
        send(
            {
                "t": "error",
                "sid": sid,
                "error": {
                    "code": "not_both_ways",
                    "message": f"{self.name} linked this server: its sessions "
                    "are reached by agents and calls, not watched from here",
                },
            }
        )

    def unsub(self, sid: str) -> None:
        pass

    def watch(self, sid: str, log_id: str | None) -> None:
        pass

    def drop(self, prefix: str) -> None:
        pass

    def wire(self) -> dict:
        return {"name": self.name, "state": self.state, "since": self.since}


class Links:
    """Every link this server holds, following ``links.json``, and the ways
    back that servers linking this one with trust opened (``Backlink``)."""

    def __init__(
        self,
        state_root: Path,
        own: str,
        user: str,
        publish: Callable[[str, list[dict]], None],
    ) -> None:
        self.store = LinkStore(state_root / "links.json")
        self.own, self.user = own, user
        self._publish = publish
        self._links: dict[str, Link] = {}
        self._back: dict[str, Backlink] = {}
        # Runs a trusted far server's call here (app.py sets it).
        self.answer: Answer | None = None
        self._entries: dict[str, dict] = {}
        self._stamp: tuple[int, int] | None = None
        self._watch: asyncio.Task | None = None

    def boot(self) -> None:
        self._sync()
        self._watch = asyncio.get_running_loop().create_task(self._follow())

    async def shutdown(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
        for link in list(self._links.values()):
            await link.stop()
        self._links.clear()

    async def _follow(self) -> None:
        while True:
            await asyncio.sleep(WATCH_EVERY_S)
            if self.store.stamp() != self._stamp:
                self._sync()

    def _sync(self) -> None:
        self._stamp = self.store.stamp()
        wanted = {e["name"]: e for e in self.store.load()}
        for name in list(self._links):
            e, old = wanted.get(name), self._entries.get(name)
            if (
                e is None
                or old is None
                or (e["url"], e["token"], e.get("trusted") is True)
                != (old["url"], old["token"], old.get("trusted") is True)
            ):
                link = self._links.pop(name)
                self._entries.pop(name, None)
                asyncio.get_running_loop().create_task(link.stop())
        for name, e in wanted.items():
            if name in self._links or name == self.own:
                continue
            link = Link(
                name,
                e["url"],
                e["token"],
                self.own,
                self.user,
                self._changed,
                trusted=e.get("trusted") is True,
                answer=self.answer,
            )
            self._links[name], self._entries[name] = link, e
            link.start()
        self._changed()

    def _changed(self) -> None:
        self._publish("links", [{"set": self.wire()}])

    def get(self, name: str) -> Link | Backlink | None:
        """The link to ``name``, or the way back to it when it linked this
        server with trust."""
        return self._links.get(name) or self._back.get(name)

    def outbound(self, name: str) -> Link | None:
        return self._links.get(name)

    def links(self) -> list[Link]:
        return list(self._links.values())

    def up(self) -> list[Link | Backlink]:
        """Every server reachable now, through a link or a way back."""
        return [
            link
            for link in [*self._links.values(), *self._back.values()]
            if link.state == "linked"
        ]

    def trusted(self, name: str | None) -> bool:
        """Whether this server and ``name`` trust each other right now: a
        link the far server granted trust, or a way back it opened."""
        if name is None:
            return False
        out = self._links.get(name)
        if out is not None:
            return out.state == "linked" and out.granted
        back = self._back.get(name)
        return back is not None and back.state == "linked"

    def attach(self, back: Backlink) -> None:
        """A server linked this one with trust; a newer socket replaces an
        older one of the same server."""
        old = self._back.get(back.name)
        if old is not None and old is not back:
            old.lost()
        self._back[back.name] = back
        self._changed()

    def detach(self, back: Backlink) -> None:
        if self._back.get(back.name) is back:
            del self._back[back.name]
            self._changed()

    def incoming(self) -> list[dict]:
        """The servers that linked this one with trust, for Settings."""
        return [b.wire() for b in self._back.values()]

    def wire(self) -> list[dict]:
        return [link.wire() for link in self._links.values()]

    def check(self, name: str, url: str) -> None:
        """Refuse a name this server cannot link under, or a URL that is not one."""
        if not NAME.match(name):
            raise OpError("bad_link", f"{name!r} is not a server name")
        if name == self.own:
            raise OpError("bad_link", f"{name} is this server")
        if any(e["name"] == name for e in self.store.load()):
            raise OpError("bad_link", f"a link named {name} exists; remove it first")
        try:
            ws_url(url)
        except ValueError as e:
            raise OpError("bad_link", str(e)) from e

    def add(self, name: str, url: str, token: str, trusted: bool = False) -> None:
        self.check(name, url)
        entries = self.store.load()
        entries.append(
            {
                "name": name,
                "url": base_url(url),
                "token": token,
                "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            | ({"trusted": True} if trusted else {})
        )
        self.store.save(entries)
        self._sync()

    def trust(self, name: str, on: bool) -> None:
        """Ask for trust on a link, or stop asking; the link reconnects."""
        entries = self.store.load()
        if not any(e["name"] == name for e in entries):
            raise OpError("no_link", f"no link named {name}")
        for e in entries:
            if e["name"] == name:
                e.pop("trusted", None)
                if on:
                    e["trusted"] = True
        self.store.save(entries)
        self._sync()

    def remove(self, name: str) -> None:
        entries = self.store.load()
        if not any(e["name"] == name for e in entries):
            raise OpError("no_link", f"no link named {name}")
        self.store.save([e for e in entries if e["name"] != name])
        self._sync()
