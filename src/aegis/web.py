"""HTTP and the one websocket.

Static files are public: they hold no secrets. The websocket is not, because
any page open in the browser can open a socket to localhost and would
otherwise be able to prompt an agent running with full permission. So a
socket must come from an allowed ``Host`` with a matching ``Origin`` (which
also stops DNS rebinding, where the attacker's name resolves to 127.0.0.1),
and must prove it holds the server's token within 5 s: in its ``hello``, or
as the HttpOnly cookie ``GET /?token=`` and ``POST /login`` set, so no script
on the page ever holds the token.

Behind a reverse proxy the browser sends the public name and an https origin,
which the loopback rule refuses. ``serve --origin https://dev.example`` names
one such origin: a socket is accepted when its ``Host`` is that origin's host
and its ``Origin`` is that origin, exactly. The token is still required.

A socket with no ``Origin`` comes from a program, never a browser, which always
sends one; it is accepted only as a link from another aegis server, whose
``hello`` says ``link: {server, user}`` and carries the token (links.py). A link
socket keys its subscriptions by ``sid``, so it can carry several to one
channel, may not open files on this server's desktop, and is not relayed on.

A browser message naming another ``server`` goes down the link to it; what
comes back gets ``server`` stamped on it. Agents' sent files on a linked server
are streamed through ``/via/<server>/files/…``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
import secrets
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
import httpx
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import files
from .app import App
from .channels import Sub
from .links import PROTO
from .ops import Caller, OpError

log = logging.getLogger("aegis.web")

HELLO_TIMEOUT_S = 5.0
CLIENT_DIR = Path(__file__).parent / "client"

# Close codes, in the 4000 range websockets leave to applications.
BAD_ORIGIN = 4403
BAD_TOKEN = 4401
NO_HELLO = 4408
BAD_PROTO = 4400

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
COOKIE_MAX_AGE_S = 365 * 24 * 3600


def cookie_name(state_root: Path) -> str:
    """The session cookie's name. Cookies ignore ports, so two servers on one
    host would overwrite each other's under one name; this one is per state
    root, as the token is."""
    return "aegis_" + hashlib.sha256(str(state_root).encode()).hexdigest()[:8]


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


def _loggable(params: object) -> object:
    """Params as the log may see them: a token never reaches a log line, and
    an upload's chunk is logged as its length."""
    if not isinstance(params, dict):
        return params
    out = dict(params)
    if "token" in out:
        out["token"] = "***"
    if isinstance(out.get("data"), str):
        out["data"] = f"<{len(out['data'])} chars>"
    return out


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
    cookie = cookie_name(app.roots.state_root)

    def valid(given: object) -> bool:
        return isinstance(given, str) and hmac.compare_digest(given, token)

    def signed_in(response: Response, request) -> Response:
        """``response`` carrying the cookie: HttpOnly, so no script on the page
        can read the token; Secure behind an https origin."""
        origin = public.get(request.headers.get("host", ""), "")
        response.set_cookie(
            cookie,
            token,
            max_age=COOKIE_MAX_AGE_S,
            path="/",
            httponly=True,
            samesite="strict",
            secure=origin.startswith("https://"),
        )
        return response

    async def index(request):
        """The page. ``?token=`` from the URL `aegis serve` printed becomes the
        cookie, and the address bar loses it; a wrong one says so."""
        given = request.query_params.get("token")
        if given is not None:
            if valid(given):
                return signed_in(RedirectResponse("/", status_code=303), request)
            return RedirectResponse("/?refused=1", status_code=303)
        return FileResponse(
            CLIENT_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def login(request):
        """A pasted token, for a browser that never opened the printed URL."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        given = body.get("token") if isinstance(body, dict) else None
        if not valid(given):
            return JSONResponse({"error": "bad_token"}, status_code=401)
        return signed_in(Response(status_code=204), request)

    manifest = {
        "name": f"aegis · {app.server_name}",
        "short_name": "aegis",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#11100e",
        "theme_color": "#0b0a09",
        "icons": [
            {
                "src": "/static/icons/aegis-192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": "/static/icons/aegis-512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
            {
                "src": "/static/icons/aegis-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }

    async def webmanifest(request):
        """What Chrome installs: the server's name, so zion and the VPS differ
        on a home screen. No service worker: Chrome installs from its menu
        without one since 108 on Android, and a caching one could serve a stale
        client after an upgrade."""
        return JSONResponse(manifest, media_type="application/manifest+json")

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

    async def via_file(request):
        """A sent file on a linked server, streamed from it. The id stays the
        secret; the headers are this server's own, from the file's name
        (files.via_headers), because the far server is not trusted to say how
        its bytes may run on this origin."""
        p = request.path_params
        host = request.headers.get("host", "")
        link = app.links.get(p["server"])
        if link is None or not (host in allowed_hosts or host in public):
            return PlainTextResponse("Not Found", status_code=404)
        download = request.query_params.get("download") == "1"
        url = f"{link.url}{files.url(p['file_id'], p['name'])}"
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
        try:
            # Identity: the bytes go out as they arrive, under this server's
            # own Content-Type, so they must not be compressed on the way in.
            upstream = await client.send(
                client.build_request(
                    "GET", url, headers={"accept-encoding": "identity"}
                ),
                stream=True,
            )
        except httpx.HTTPError:
            await client.aclose()
            return PlainTextResponse("Bad Gateway", status_code=502)
        if upstream.status_code != 200:
            await upstream.aclose()
            await client.aclose()
            return PlainTextResponse("Not Found", status_code=404)

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(body(), headers=files.via_headers(p["name"], download))

    async def ws(websocket: WebSocket) -> None:
        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin")
        local = host in allowed_hosts and origin == f"http://{host}"
        program = origin is None and (host in allowed_hosts or host in public)
        if not local and not program and public.get(host) != origin:
            await websocket.close(code=BAD_ORIGIN)
            return
        await websocket.accept()
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), HELLO_TIMEOUT_S)
        except (TimeoutError, ValueError, WebSocketDisconnect):
            with contextlib.suppress(Exception):
                await websocket.close(code=NO_HELLO)
            return
        if not isinstance(hello, dict):
            hello = {}
        # Only a program (no Origin) can be a link: a browser saying so is not.
        link = hello.get("link") if program else None
        via = link.get("server") if isinstance(link, dict) else None
        if program and not isinstance(via, str):
            await websocket.close(code=BAD_ORIGIN)  # a program that is not a link
            return
        given = hello.get("token")
        if not isinstance(given, str) and not program:
            given = websocket.cookies.get(cookie)
        if hello.get("t") != "hello" or not valid(given):
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
        caller = Caller(
            "user", desktop=desktop, link=via if isinstance(via, str) else None
        )
        out: asyncio.Queue[dict] = asyncio.Queue()
        # This socket's subscriptions forwarded down links: <bid>:<channel>.
        bid = secrets.token_hex(6)

        async def writer() -> None:
            while True:
                msg = await out.get()
                await websocket.send_text(json.dumps(msg, ensure_ascii=False))

        def far(server: object):
            """The link a message names, or None when it is for this server."""
            if server is None or server == app.server_name:
                return None
            if caller.link is not None:
                raise OpError("not_relayed", "a link is not relayed to another server")
            found = app.links.get(str(server))
            if found is None:
                raise OpError(
                    "unknown_server", f"this server links no server named {server}"
                )
            return found

        async def handle_call(msg: dict) -> None:
            reply: dict = {"t": "reply", "id": msg.get("id")}
            server = msg.get("server")
            if server is not None:
                reply["server"] = server
            op = str(msg.get("op"))
            t0 = time.monotonic()
            log.info("call %s %s", op, _loggable(msg.get("params")))
            try:
                link = far(server)
                if link is not None:
                    reply.update(await link.call_raw(op, msg.get("params")))
                else:
                    reply["result"] = await app.registry.call(
                        op, msg.get("params"), caller
                    )
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
                "server": app.server_name,
                "native": desktop,
            }
        )
        write_task = asyncio.create_task(writer())
        subs: dict[str, Sub] = {}
        forwarded: dict[str, tuple] = {}  # sid -> (link, server, channel)
        calls: set[asyncio.Task] = set()

        def restamp(server: str, channel: str):
            def send(m: dict) -> None:
                m = {k: v for k, v in m.items() if k != "sid"}
                out.put_nowait({**m, "server": server, "channel": channel})

            return send

        try:
            while True:
                msg = await websocket.receive_json()
                t = msg.get("t") if isinstance(msg, dict) else None
                if t == "call":
                    task = asyncio.create_task(handle_call(msg))
                    calls.add(task)
                    task.add_done_callback(calls.discard)
                elif t in ("sub", "unsub"):
                    ch = str(msg.get("channel"))
                    server = msg.get("server")
                    # A link names each subscription; a browser's is its channel.
                    key = str(msg.get("sid") or ch) if caller.link else ch
                    try:
                        link = far(server)
                    except OpError as e:
                        out.put_nowait(
                            {
                                "t": "error",
                                "channel": ch,
                                "server": server,
                                "error": {"code": e.code, "message": e.message},
                            }
                        )
                        continue
                    if link is not None:
                        sid = f"{bid}:{server}:{ch}"
                        if sid in forwarded:
                            forwarded.pop(sid)[0].unsub(sid)
                        if t == "sub":
                            since = msg.get("since")
                            if not isinstance(since, int) or isinstance(since, bool):
                                since = None
                            forwarded[sid] = (link, server, ch)
                            link.sub(sid, ch, since, restamp(str(server), ch))
                        continue
                    if key in subs:
                        app.channels.unsubscribe(subs.pop(key))
                    if t == "unsub":
                        continue
                    since = msg.get("since")
                    if not isinstance(since, int) or isinstance(since, bool):
                        since = None
                    send = out.put_nowait
                    if caller.link and msg.get("sid"):
                        sid = msg["sid"]

                        def send(m: dict, sid=sid) -> None:
                            out.put_nowait({**m, "sid": sid})

                    try:
                        subs[key] = app.channels.subscribe(ch, send, since)
                    except OpError as e:
                        send(
                            {
                                "t": "error",
                                "channel": ch,
                                "error": {"code": e.code, "message": e.message},
                            }
                        )
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            for sub in subs.values():
                app.channels.unsubscribe(sub)
            for sid, (link, _, _) in forwarded.items():
                link.unsub(sid)
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
            Route("/login", login, methods=["POST"]),
            Route("/manifest.webmanifest", webmanifest),
            Mount("/static", ClientFiles(directory=CLIENT_DIR)),
            Route("/files/{file_id}/{name}", sent_file),
            Route("/via/{server}/files/{file_id}/{name}", via_file),
            Route("/dictation/{pin}/{name}", dictation_file),
            WebSocketRoute("/ws", ws),
            *app.mcp_app.routes,
        ],
        lifespan=lifespan,
    )
