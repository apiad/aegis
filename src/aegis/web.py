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
import json
import logging
import os
import re
import time
import secrets
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
import httpx
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import exit as exit_
from . import files, fs_paths
from .app import App
from .as_user import Place
from .channels import Sub
from .links import PROTO, Backlink
from .ops import Caller, OpError
from .version import running
from .tokens import load_or_create_token  # noqa: F401 — cli and tests import it from here

log = logging.getLogger("aegis.web")

HELLO_TIMEOUT_S = 5.0
CLIENT_DIR = Path(__file__).parent / "client"
# The service worker lives outside CLIENT_DIR, so /static never serves it: it is
# served only from the root, where its scope is the whole client.
SERVICE_WORKER = Path(__file__).parent / "sw.js"


POINT_TAG = b'<script src="/static/js/pagepoint.js"></script>'


def inject_point(body: bytes) -> bytes:
    """An HTML page with the pointing script (js/pagepoint.js) before its last
    ``</body>``, or at its end. A tag, not the script inline: previews are
    cached as immutable, and a static script follows the client's version.
    Idempotent, so a page that already has it gets no second one."""
    if POINT_TAG in body:
        return body
    low = body.lower()
    at = low.rfind(b"</body>")
    # A "</body>" before the last </script> is inside a script's text (the
    # page left out its own, which HTML allows): the tag goes at the end.
    if at < 0 or at < low.rfind(b"</script>"):
        return body + POINT_TAG
    return body[:at] + POINT_TAG + body[at:]


def _open_as(place: Place, rel: str, download: bool):
    """On ``place``'s runner: the headers for ``rel``, and its bytes when an
    HTML preview needs the pointing script, else an open descriptor and its
    size. Raises OpError or OSError when the file is not there to serve."""
    path = fs_paths.resolve(place.root, place.guard, rel)
    if not path.is_file():
        raise OpError("not_a_file", f"{rel} is a folder")
    headers = {**files.headers(path, download), "Cache-Control": "no-store"}
    if not download and files.classify(path)[1] == "html":
        return headers, inject_point(path.read_bytes()), None
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    return headers, fd, os.fstat(fd).st_size


_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 256 * 1024


def _fd_response(request, headers: dict, body: bytes | int, size: int | None):
    """What ``_open_as`` opened, as a response: bytes as they are, or the
    descriptor streamed, with one byte range when the request asks for one
    (a video seeks, a PDF reads pages), as FileResponse does for a path."""
    headers = dict(headers)
    media = headers.pop("Content-Type")
    if isinstance(body, bytes):
        return Response(body, media_type=media, headers=headers)
    fd = body
    assert size is not None
    start, end, status = 0, size - 1, 200
    m = _RANGE.fullmatch(request.headers.get("range", "").strip())
    first, last = (
        (int(m[1]) if m and m[1] else None),
        (int(m[2]) if m and m[2] else None),
    )
    # A range whose end is before its start is invalid and ignored (RFC 9110).
    if (
        m
        and (first is not None or last is not None)
        and not (first is not None and last is not None and last < first)
    ):
        if first is not None:
            start, end = first, min(size - 1 if last is None else last, size - 1)
        else:
            start = max(size - (last or 0), 0)
        if start > end:
            os.close(fd)
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{size}"}
            )
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Accept-Ranges"] = "bytes"
    headers["Content-Length"] = str(end - start + 1)

    async def stream():
        at = start
        while at <= end:
            n = min(_CHUNK, end - at + 1)
            chunk = await asyncio.to_thread(os.pread, fd, n, at)
            if not chunk:
                return
            yield chunk
            at += len(chunk)

    return _FdStream(
        fd, stream(), status_code=status, media_type=media, headers=headers
    )


class _FdStream(StreamingResponse):
    """A StreamingResponse that closes its descriptor when the response ends,
    however it ends: a generator that never started has no finally to run."""

    def __init__(self, fd: int, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.fd = fd

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self.fd)


def _file_response(path: Path, download: bool, extra: dict | None = None):
    """A sent or a Files file: its bytes, under the headers files.py decides.
    An HTML preview gets the pointing script; a download is the file as it is."""
    headers = {**files.headers(path, download), **(extra or {})}
    media = headers.pop("Content-Type")
    if not download and files.classify(path)[1] == "html":
        return Response(
            inject_point(path.read_bytes()), media_type=media, headers=headers
        )
    return FileResponse(path, media_type=media, headers=headers)


def ws_protocol():
    """The websocket protocol ``aegis serve`` hands uvicorn: its own, with
    permessage-deflate at the standard 32 KB window. uvicorn's sans-I/O
    protocol, the default since 0.4x, offers a 4 KB window and memLevel 5,
    which compressed an idle client's frames 4 to 1 where 32 KB does 7 to 1
    (#525). The cost is about 300 KB of zlib state per open socket. An uvicorn
    without that protocol keeps its default, whose deflate takes the
    browser's offer."""
    try:
        from uvicorn.protocols.websockets.websockets_sansio_impl import (
            WebSocketsSansIOProtocol,
        )
    except ImportError:
        return "auto"
    from websockets.extensions.permessage_deflate import ServerPerMessageDeflateFactory
    from websockets.server import ServerProtocol

    class Protocol(WebSocketsSansIOProtocol):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            if self.config.ws_per_message_deflate:
                self.conn = ServerProtocol(
                    extensions=[ServerPerMessageDeflateFactory()],
                    max_size=self.config.ws_max_size,
                    logger=self.conn.logger,
                )

    return Protocol


def client_files(version: str) -> dict:
    """The client as the service worker keeps it: every file under CLIENT_DIR by
    the URL the page asks for it, plus the manifest, and a build id hashed from
    each file's path, size and mtime, the worker's own and the version, so a
    change to sw.js alone is a new build with a cache of its own. Computed per
    request: about a hundred ``stat`` calls, and an edit to the client while
    the server runs is a new build at once, which is what the browser's byte
    comparison of ``/sw.js`` needs to see."""
    h = hashlib.sha256(version.encode())
    files = []
    for path in sorted(CLIENT_DIR.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.name.startswith(".")
        ):
            continue
        rel = path.relative_to(CLIENT_DIR).as_posix()
        st = path.stat()
        h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        files.append(
            {
                "url": "/" if rel == "index.html" else f"/static/{rel}",
                "bytes": st.st_size,
            }
        )
    st = SERVICE_WORKER.stat()
    h.update(f"sw.js\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return {"build": h.hexdigest()[:12], "version": version, "files": files}


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


_LOG_FIELD = 200


def _capped(value: object) -> object:
    """``value``, or its first ``_LOG_FIELD`` characters and its size when it
    is longer. A 64 KB artifact submit logged whole could fill a stdout pipe
    nobody drained and block the server on its own log line (#277)."""
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= _LOG_FIELD:
        return value
    return f"{text[:_LOG_FIELD]}… <{len(text)} chars>"


def _loggable(params: object) -> object:
    """Params as the log may see them: a token never reaches a log line, an
    upload's chunk is logged as its length, and every other field is capped."""
    if not isinstance(params, dict):
        return _capped(params)
    out = dict(params)
    data = out.get("data")
    out = {k: _capped(v) for k, v in out.items()}
    for masked in ("token", "secret"):
        if masked in out:
            out[masked] = "***"
    if isinstance(data, str):
        out["data"] = f"<{len(data)} chars>"
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
    app.push.origins = list(public.values())  # the VAPID contact (push.py)
    cookie = cookie_name(app.roots.state_root)

    # ``token`` is the admin's (tokens.py); any token the store knows signs in.
    app.principals.use_admin(token)

    def valid(given: object) -> bool:
        return app.principals.resolve(given) is not None

    def signed_in(response: Response, request, given: str) -> Response:
        """``response`` carrying the cookie with the token that was presented:
        HttpOnly, so no script on the page can read it; Secure behind an https
        origin."""
        origin = public.get(request.headers.get("host", ""), "")
        response.set_cookie(
            cookie,
            given,
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
                return signed_in(RedirectResponse("/", status_code=303), request, given)
            return RedirectResponse("/?refused=1", status_code=303)
        # The build the page belongs to, which the welcome repeats: a page
        # older than the server takes the new build by itself (#499).
        html = (CLIENT_DIR / "index.html").read_text()
        return HTMLResponse(
            html.replace("__AEGIS_BUILD__", listing()["build"]),
            headers={"Cache-Control": "no-cache"},
        )

    async def login(request):
        """A pasted token, for a browser that never opened the printed URL."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        given = body.get("token") if isinstance(body, dict) else None
        if not isinstance(given, str) or not valid(given):
            return JSONResponse({"error": "bad_token"}, status_code=401)
        return signed_in(Response(status_code=204), request, given)

    manifest = {
        "name": f"aegis · {app.server_name}",
        "short_name": "aegis",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#11100e",
        "theme_color": "#0b0a09",
        # Android lists the installed app as a share target (#735). The service
        # worker takes the POST (sw.js stash); share_fallback below when none runs.
        "share_target": {
            "action": "/share",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {
                "title": "title",
                "text": "text",
                "url": "url",
                "files": [{"name": "files", "accept": ["*/*"]}],
            },
        },
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

    version = running()["version"] or "dev"

    def listing() -> dict:
        doc = client_files(version)
        doc["files"].append(
            {"url": "/manifest.webmanifest", "bytes": len(json.dumps(manifest))}
        )
        doc["bytes"] = sum(f["bytes"] for f in doc["files"])
        return doc

    async def share_fallback(request):
        """A share that reached the server: no service worker took it, as when
        the one running predates share targets. The body is never read, since
        the route takes no token; the person gets an empty New session form."""
        return RedirectResponse("/#new", status_code=303)

    async def files_list(request):
        """What the service worker downloads to open the client offline (sw.js)."""
        return JSONResponse(listing(), headers={"Cache-Control": "no-cache"})

    async def webmanifest(request):
        """What Chrome installs: the server's name, so two servers differ on a
        home screen. The service worker is ``sw.js``, and it keeps this
        manifest with the build it belongs to."""
        return JSONResponse(manifest, media_type="application/manifest+json")

    async def service_worker(request):
        """The service worker, from the root so its scope is the whole client,
        with this build's id written in: a new build changes the worker's
        bytes, which is how the browser finds it. ``no-cache`` makes the
        browser revalidate it on every update check."""
        text = SERVICE_WORKER.read_text().replace("__AEGIS_BUILD__", listing()["build"])
        return Response(
            text, media_type="text/javascript", headers={"Cache-Control": "no-cache"}
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
        return _file_response(path, request.query_params.get("download") == "1")

    async def fs_file(request):
        """A file under the root, through a ticket fs.ticket or fs.read issued
        (fs_ops.py). The path is resolved again now, so a file replaced by a
        symlink out of the root since the ticket was issued is refused, and
        the bytes are the file's as it is now, never cached."""
        host = request.headers.get("host", "")
        found = app.fs_tickets.find(request.path_params["ticket"])
        if found is None or not (host in allowed_hosts or host in public):
            return PlainTextResponse("Not Found", status_code=404)
        rel, place = found
        download = request.query_params.get("download") == "1"
        if place is not None and place.user is not None:
            # A person's file in multi-user mode: opened as them, then served
            # from the open descriptor, which the server could not reopen.
            try:
                opened = await place.run(_open_as, place, rel, download)
            except (OpError, OSError):
                return PlainTextResponse("Not Found", status_code=404)
            return _fd_response(request, *opened)
        try:
            path = fs_paths.resolve(app.roots.config_root, app.roots.state_root, rel)
        except OpError:
            return PlainTextResponse("Not Found", status_code=404)
        if not path.is_file():
            return PlainTextResponse("Not Found", status_code=404)
        return _file_response(path, download, {"Cache-Control": "no-store"})

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

    async def speech_file(request):
        """Phonon's pinned files (speech.py), kept forever like dictation's."""
        p = request.path_params
        path = app.speech.file(p["pin"], p["name"])
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
        if (
            link is None
            or link.url is None
            or not (host in allowed_hosts or host in public)
        ):
            return PlainTextResponse("Not Found", status_code=404)
        download = request.query_params.get("download") == "1"
        # A download asks the far server for one too: its preview of a page
        # carries the pointing script, and a download is the file's own bytes.
        url = f"{link.url}{files.url(p['file_id'], p['name'])}"
        if download:
            url += "?download=1"
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

        headers = files.via_headers(p["name"], download)
        if not download and files.classify_name(p["name"])[1] == "html":
            # The script here too, once: a far server older than it sends none.
            page = b"".join([chunk async for chunk in body()])
            return Response(
                inject_point(page),
                media_type=headers.pop("Content-Type"),
                headers=headers,
            )
        return StreamingResponse(body(), headers=headers)

    async def tunnel(websocket: WebSocket) -> None:
        """One stream of a trusted link's exit (exit.py). A program's, like a
        link's socket: a browser always sends an ``Origin``."""
        host = websocket.headers.get("host", "")
        if websocket.headers.get("origin") is not None or (
            host not in allowed_hosts and host not in public
        ):
            await websocket.close(code=BAD_ORIGIN)
            return
        scheme, _, given = websocket.headers.get("authorization", "").partition(" ")
        via = websocket.headers.get(exit_.LINK_HEADER, "")

        def refusal() -> str | None:
            principal = (
                app.principals.resolve(given) if scheme.lower() == "bearer" else None
            )
            if principal is None:
                return "bad token"
            return app.trust_refusal(principal, via) if via else "no link named"

        await exit_.relay(
            websocket,
            websocket.headers.get(exit_.TARGET_HEADER, ""),
            refusal,
            lambda n: app.count_exit(via, n),
        )

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
        principal = app.principals.resolve(given)
        if hello.get("t") != "hello" or principal is None:
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
            "user",
            desktop=desktop,
            link=via if isinstance(via, str) else None,
            principal=principal,
        )
        out: asyncio.Queue[dict] = asyncio.Queue()
        # This socket's subscriptions forwarded down links: <bid>:<channel>.
        bid = secrets.token_hex(6)
        # A link asking for trust (#684): granted, it is also this server's way
        # back to the server that opened it (links.Backlink).
        asked = isinstance(link, dict) and link.get("trusted") is True
        refused = app.trust_refusal(principal, str(via)) if asked else None
        back: Backlink | None = None
        if asked and refused is None:

            def still() -> str | None:
                if app.principals.resolve(given) != principal:
                    return "the link's token was revoked"
                return app.trust_refusal(principal, str(via))

            back = Backlink(str(via), out.put_nowait, still, app.links.detach)

        async def writer() -> None:
            while True:
                msg = await out.get()
                # A revoked token reads nothing more, even on a socket that
                # sends nothing (the reader checks the ones that do).
                if app.principals.resolve(given) != principal:
                    await websocket.close(code=BAD_TOKEN)
                    return
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
                    # A link acts on the far server as its own token, which is
                    # not this caller's: only an admin here may act through it.
                    # Subscriptions, which only read, stay open to members.
                    if not app.who(caller).is_admin:
                        raise OpError(
                            "not_for_members",
                            f"acting on {server} through this server's link is "
                            "for an admin here",
                        )
                    reply.update(await link.call_raw(op, msg.get("params")))
                else:
                    reply["result"] = await app.registry.call(
                        op, msg.get("params"), caller
                    )
            except OpError as e:
                reply["error"] = {"code": e.code, "message": e.message}
                if e.data is not None:
                    reply["error"]["data"] = e.data
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
                "build": listing()["build"],
                # A spawn through a link may say its group (#586): a link
                # sends it only to a server that says this.
                "groups": True,
                "native": desktop,
                "me": {
                    "name": principal.name,
                    "role": principal.role,
                    # Its quota lines, drawn on its gauges (quota_gate.py).
                    "policy": app.principals.policy_of(principal.name),
                },
                # Whose a session with no owner is: the admin's (tokens.py).
                "owner": app.principals.owner,
            }
            | (
                {"trusted": back is not None}
                | ({"trust_refused": refused} if refused else {})
                if asked
                else {}
            )
        )
        if back is not None:
            app.links.attach(back)
        write_task = asyncio.create_task(writer())
        subs: dict[str, Sub] = {}
        forwarded: dict[str, tuple] = {}  # sid -> (link, server, channel)
        watched_far: set[str] = set()  # servers told this socket watches there
        calls: set[asyncio.Task] = set()

        def restamp(server: str, channel: str):
            def send(m: dict) -> None:
                m = {k: v for k, v in m.items() if k != "sid"}
                out.put_nowait({**m, "server": server, "channel": channel})

            return send

        try:
            while True:
                msg = await websocket.receive_json()
                # A token revoked while this socket was open stops here, at its
                # next message, not at its next connection.
                if app.principals.resolve(given) != principal:
                    await websocket.close(code=BAD_TOKEN)
                    break
                t = msg.get("t") if isinstance(msg, dict) else None
                if t == "call":
                    task = asyncio.create_task(handle_call(msg))
                    calls.add(task)
                    task.add_done_callback(calls.discard)
                elif t == "rreply" and back is not None:
                    back.resolve(msg)
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
                            link.sub(
                                sid,
                                ch,
                                since,
                                restamp(str(server), ch),
                                merge=msg.get("merge") is True,
                            )
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
                        subs[key] = app.channels.subscribe(
                            ch,
                            send,
                            since,
                            merge=msg.get("merge") is True,
                            view=app.view_of(caller),
                        )
                    except OpError as e:
                        send(
                            {
                                "t": "error",
                                "channel": ch,
                                "error": {"code": e.code, "message": e.message},
                            }
                        )
                elif t == "watch":
                    # Which session this page shows while it is visible (park.py).
                    sess = msg.get("session")
                    log_id = (
                        sess if isinstance(sess, str) and msg.get("visible") else None
                    )
                    server = msg.get("server")
                    try:
                        link = far(server)
                    except OpError:
                        continue
                    if link is not None:
                        link.watch(f"{bid}:{server}", log_id)
                        watched_far.add(server)
                    elif caller.link and isinstance(msg.get("sid"), str):
                        app.watchers.watch(f"{bid}:{msg['sid']}", log_id)
                    else:
                        app.watchers.watch(bid, log_id)
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            for sub in subs.values():
                app.channels.unsubscribe(sub)
            for sid, (link, _, _) in forwarded.items():
                link.unsub(sid)
            app.watchers.drop(bid)
            for server in watched_far:
                try:
                    link = far(server)
                except OpError:
                    continue
                if link is not None:
                    link.watch(f"{bid}:{server}", None)
            if back is not None:
                back.lost()
                app.links.detach(back)
            write_task.cancel()

    @contextlib.asynccontextmanager
    async def lifespan(_):
        # The MCP app keeps its own session manager running for the server's life.
        async with app.mcp_app.router.lifespan_context(app.mcp_app):
            await app.boot()
            yield
            await app.shutdown()

    web = Starlette(
        routes=[
            Route("/", index),
            Route("/login", login, methods=["POST"]),
            Route("/share", share_fallback, methods=["POST"]),
            Route("/manifest.webmanifest", webmanifest),
            Route("/sw.js", service_worker),
            Route("/client-files.json", files_list),
            Mount("/static", ClientFiles(directory=CLIENT_DIR)),
            Route("/files/{file_id}/{name}", sent_file),
            Route("/fs/{ticket}/{name}", fs_file),
            Route("/via/{server}/files/{file_id}/{name}", via_file),
            Route("/dictation/{pin}/{name}", dictation_file),
            Route("/speech/{pin}/{name:path}", speech_file),
            WebSocketRoute("/ws", ws),
            WebSocketRoute("/tunnel", tunnel),
            *app.mcp_app.routes,
        ],
        lifespan=lifespan,
    )
    web.state.aegis = app
    return web
