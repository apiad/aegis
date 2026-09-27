"""The Starlette app `aegis web` serves.

Static files are public: they are the page's code, and the code holds no
secret. The page itself, and the socket behind it, need the cookie.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from aegis.webterm.auth import COOKIE, token_ok
from aegis.webterm.relay import Connect, relay

log = logging.getLogger(__name__)

_PKG_STATIC = Path(__file__).resolve().parent / "static"
_YEAR = 60 * 60 * 24 * 365
# Where a browser keeps a `Secure` cookie over plain http. Anywhere else it
# drops it silently, so the login redirect lands back on a 401.
_SECURE_OVER_HTTP = ("localhost", "127.0.0.1", "::1", "[::1]")


def _cookie_would_be_dropped(request) -> bool:
    return (
        request.url.scheme != "https" and request.url.hostname not in _SECURE_OVER_HTTP
    )


class _StarletteBrowser:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def receive(self):
        message = await self._ws.receive()
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("bytes") is not None:
            return message["bytes"]
        return message.get("text")

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def send_text(self, text: str) -> None:
        await self._ws.send_text(text)


def build_webterm_app(
    *, token: str, connect: Connect, static_dir: Path | None = None
) -> Starlette:
    static = Path(static_dir) if static_dir is not None else _PKG_STATIC

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def service_worker(request):
        # No cookie check: an old worker's update check carries no cookie of
        # ours, and a 401 would leave it installed and intercepting for ever.
        return FileResponse(
            static / "service-worker.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    async def index(request):
        presented = request.query_params.get("t")
        if presented is not None:
            if not token_ok(presented, token):
                return PlainTextResponse("unauthorized", status_code=401)
            response = RedirectResponse("/", status_code=303)
            # Lax, not Strict: a Strict cookie is withheld from the redirect
            # when the login link was opened from another site (webmail, a
            # chat client in a browser), so the first visit would be a 401.
            # Lax already keeps the cookie off a cross-site WebSocket, and the
            # Origin check in `term` covers same-site hosts.
            response.set_cookie(
                COOKIE, token, max_age=_YEAR, httponly=True, secure=True, samesite="lax"
            )
            return response
        if not token_ok(request.cookies.get(COOKIE), token):
            why = "unauthorized: open the URL `aegis web` printed"
            if _cookie_would_be_dropped(request):
                # Saying "open the URL you just opened" is the unhelpful
                # answer here: the URL worked and the browser threw the
                # cookie away, which only https or localhost prevents.
                why = (
                    "unauthorized: this login sets a Secure cookie, which "
                    f"your browser keeps over plain http only for "
                    f"{', '.join(_SECURE_OVER_HTTP[:2])}. Reach "
                    f"{request.url.hostname} over https (a reverse proxy in "
                    "front of `aegis web`), or open it on the machine itself."
                )
            return PlainTextResponse(why, status_code=401)
        return FileResponse(
            static / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def term(ws: WebSocket) -> None:
        # Refused before accept(): an unauthenticated client never reaches
        # the relay, so it never reaches the daemon.
        if not token_ok(ws.cookies.get(COOKIE), token):
            await ws.close(code=4401)
            return
        # Browsers always send Origin on a WebSocket; a client without one
        # is not a browser and cannot be carrying someone else's cookie.
        origin = ws.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != ws.headers.get("host"):
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            await relay(_StarletteBrowser(ws), connect)
        except Exception:  # noqa: BLE001 — an unreachable daemon ends this socket, not the server
            log.info("relay ended: daemon unreachable", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    return Starlette(
        routes=[
            Route("/", index),
            Route("/healthz", healthz),
            WebSocketRoute("/term", term),
            Route("/service-worker.js", service_worker),
            Mount("/static", app=StaticFiles(directory=static)),
        ]
    )
