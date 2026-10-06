"""HTTP and the one websocket.

Static files are public: they hold no secrets. The websocket is not, because
any page open in the browser can open a socket to localhost and would
otherwise be able to prompt an agent running with full permission. So a
socket must come from an allowed ``Host`` with a matching ``Origin`` (which
also stops DNS rebinding, where the attacker's name resolves to 127.0.0.1),
and must say ``hello`` with the server's token within 5 s.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socket
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .app import App
from .channels import Sub
from .ops import OpError

PROTO = 1
HELLO_TIMEOUT_S = 5.0
CLIENT_DIR = Path(__file__).parent / "client"

# Close codes, in the 4000 range websockets leave to applications.
BAD_ORIGIN = 4403
BAD_TOKEN = 4401
NO_HELLO = 4408
BAD_PROTO = 4400


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


def build_web(app: App, token: str, allowed_hosts: set[str]) -> Starlette:
    async def index(request):
        return FileResponse(
            CLIENT_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def ws(websocket: WebSocket) -> None:
        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin", "")
        if host not in allowed_hosts or origin != f"http://{host}":
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

        out: asyncio.Queue[dict] = asyncio.Queue()

        async def writer() -> None:
            while True:
                msg = await out.get()
                await websocket.send_text(json.dumps(msg, ensure_ascii=False))

        async def handle_call(msg: dict) -> None:
            reply: dict = {"t": "reply", "id": msg.get("id")}
            try:
                reply["result"] = await app.registry.call(
                    str(msg.get("op")), msg.get("params")
                )
            except OpError as e:
                reply["error"] = {"code": e.code, "message": e.message}
            except Exception as e:  # an operation bug must not kill the socket
                reply["error"] = {
                    "code": "internal",
                    "message": f"{type(e).__name__}: {e}",
                }
            out.put_nowait(reply)

        out.put_nowait({"t": "welcome", "proto": PROTO, "server": socket.gethostname()})
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
                    if ch in subs:
                        app.channels.unsubscribe(subs.pop(ch))
                    try:
                        subs[ch] = app.channels.subscribe(ch, out.put_nowait)
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
        app.boot()
        yield
        await app.shutdown()

    return Starlette(
        routes=[
            Route("/", index),
            Mount("/static", ClientFiles(directory=CLIENT_DIR)),
            WebSocketRoute("/ws", ws),
        ],
        lifespan=lifespan,
    )
