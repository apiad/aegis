"""HTTP and the one websocket.

Static files are public: they hold no secrets. The websocket is not, because
any page open in the browser can open a socket to localhost and would
otherwise be able to prompt an agent running with full permission. So a
socket must come from an allowed ``Host`` with a matching ``Origin`` (which
also stops DNS rebinding, where the attacker's name resolves to 127.0.0.1),
and must say ``hello`` with the server's token within 5 s.

Behind a reverse proxy the browser sends the public name and an https origin,
which the loopback rule refuses. ``serve --origin https://dev.example`` names
one such origin: a socket is accepted when its ``Host`` is that origin's host
and its ``Origin`` is that origin, exactly. The token is still required.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import time
import secrets
import socket
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import FileResponse, PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import files
from .app import App
from .channels import Sub
from .ops import Caller, OpError

log = logging.getLogger("aegis.web")

PROTO = 2
HELLO_TIMEOUT_S = 5.0
CLIENT_DIR = Path(__file__).parent / "client"

# Close codes, in the 4000 range websockets leave to applications.
BAD_ORIGIN = 4403
BAD_TOKEN = 4401
NO_HELLO = 4408
BAD_PROTO = 4400

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


class ClientFiles(StaticFiles):
    """The client's files, revalidated on every load (``no-cache`` plus the
    ETag Starlette already sends), so an edit to the client shows on the next
    reload instead of whenever the browser's heuristic cache expires."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def load_or_create_token(state_root: Path) -> str:
    """The server's token, kept in ``<state>/token`` with mode 0600 and reused
    across restarts so an open tab survives one. Delete the file to rotate."""
    path = state_root / "token"
    if path.exists():
        return path.read_text().strip()
    state_root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def public_origin(value: str) -> str:
    """``value`` as an origin, ``scheme://host[:port]``; ValueError if it is not one."""
    u = urlsplit(value.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"{value!r} is not an http or https origin")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError(
            f"{value!r} has a path or query; an origin is a scheme and a host"
        )
    return f"{u.scheme}://{u.netloc}"


def build_web(
    app: App, token: str, allowed_hosts: set[str], origins: Iterable[str] = ()
) -> Starlette:
    # Host header -> the one origin a socket naming that host must come from.
    public = {urlsplit(o).netloc: o for o in map(public_origin, origins)}

    async def index(request):
        return FileResponse(
            CLIENT_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def sent_file(request):
        """A file an agent sent. The id is the secret (files.py); a Host the
        websocket would refuse, local or a proxy's public name, gets the same
        404 as a wrong id."""
        p = request.path_params
        host = request.headers.get("host", "")
        path = (
            files.find(app.roots.state_root, p["file_id"], p["name"])
            if host in allowed_hosts or host in public
            else None
        )
        if path is None:
            return PlainTextResponse("Not Found", status_code=404)
        headers = files.headers(path, request.query_params.get("download") == "1")
        return FileResponse(
            path, media_type=headers.pop("Content-Type"), headers=headers
        )

    async def dictation_file(request):
        """The pinned dictation files. The path changes with the pin, so a
        browser may keep them forever (dictation.py)."""
        p = request.path_params
        path = app.dictation.file(p["pin"], p["name"])
        if path is None:
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(
            path, headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )

    async def ws(websocket: WebSocket) -> None:
        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin", "")
        local = host in allowed_hosts and origin == f"http://{host}"
        if not local and public.get(host) != origin:
            await websocket.close(code=BAD_ORIGIN)
            return
        await websocket.accept()
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), HELLO_TIMEOUT_S)
        except (TimeoutError, ValueError, WebSocketDisconnect):
            with contextlib.suppress(Exception):
                await websocket.close(code=NO_HELLO)
            return
        given = hello.get("token") if isinstance(hello, dict) else None
        if (
            hello.get("t") != "hello"
            or not isinstance(given, str)
            or not hmac.compare_digest(given, token)
        ):
            await websocket.close(code=BAD_TOKEN)
            return
        if hello.get("proto") != PROTO:
            await websocket.send_json(
                {
                    "t": "error",
                    "message": f"this server speaks protocol {PROTO}, the client {hello.get('proto')}",
                }
            )
            await websocket.close(code=BAD_PROTO)
            return

        # On the server's desktop: a loopback socket (a proxy's public name
        # is a browser elsewhere) on a server with somewhere to open a file.
        desktop = (
            local and host.rpartition(":")[0] in LOOPBACK and files.opener() is not None
        )
        caller = Caller("user", desktop=desktop)
        out: asyncio.Queue[dict] = asyncio.Queue()

        async def writer() -> None:
            while True:
                msg = await out.get()
                await websocket.send_text(json.dumps(msg, ensure_ascii=False))

        async def handle_call(msg: dict) -> None:
            reply: dict = {"t": "reply", "id": msg.get("id")}
            op = str(msg.get("op"))
            t0 = time.monotonic()
            log.info("call %s %s", op, msg.get("params"))
            try:
                reply["result"] = await app.registry.call(op, msg.get("params"), caller)
            except OpError as e:
                reply["error"] = {"code": e.code, "message": e.message}
            except Exception as e:  # an operation bug must not kill the socket
                log.exception("call %s failed", op)
                reply["error"] = {
                    "code": "internal",
                    "message": f"{type(e).__name__}: {e}",
                }
            ms = (time.monotonic() - t0) * 1000
            log.info(
                "call %s done in %.0f ms %s",
                op,
                ms,
                reply.get("error", {}).get("code", "ok"),
            )
            out.put_nowait(reply)

        out.put_nowait(
            {
                "t": "welcome",
                "proto": PROTO,
                "server": socket.gethostname(),
                "native": desktop,
            }
        )
        write_task = asyncio.create_task(writer())
        subs: dict[str, Sub] = {}
        calls: set[asyncio.Task] = set()
        try:
            while True:
                msg = await websocket.receive_json()
                t = msg.get("t") if isinstance(msg, dict) else None
                if t == "call":
                    task = asyncio.create_task(handle_call(msg))
                    calls.add(task)
                    task.add_done_callback(calls.discard)
                elif t == "sub":
                    ch = str(msg.get("channel"))
                    since = msg.get("since")
                    if not isinstance(since, int) or isinstance(since, bool):
                        since = None
                    if ch in subs:
                        app.channels.unsubscribe(subs.pop(ch))
                    try:
                        subs[ch] = app.channels.subscribe(ch, out.put_nowait, since)
                    except OpError as e:
                        out.put_nowait(
                            {
                                "t": "error",
                                "channel": ch,
                                "error": {"code": e.code, "message": e.message},
                            }
                        )
                elif t == "unsub":
                    sub = subs.pop(str(msg.get("channel")), None)
                    if sub is not None:
                        app.channels.unsubscribe(sub)
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            for sub in subs.values():
                app.channels.unsubscribe(sub)
            write_task.cancel()

    @contextlib.asynccontextmanager
    async def lifespan(_):
        # The MCP app keeps its own session manager running for the server's life.
        async with app.mcp_app.router.lifespan_context(app.mcp_app):
            await app.boot()
            yield
            await app.shutdown()

    return Starlette(
        routes=[
            Route("/", index),
            Mount("/static", ClientFiles(directory=CLIENT_DIR)),
            Route("/files/{file_id}/{name}", sent_file),
            Route("/dictation/{pin}/{name}", dictation_file),
            WebSocketRoute("/ws", ws),
            *app.mcp_app.routes,
        ],
        lifespan=lifespan,
    )
