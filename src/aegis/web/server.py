"""Starlette app for the web frontend — built in the house idiom of
``aegis.remote.plane`` (raw Starlette, ``build_*`` factory, uvicorn driven by
the caller). One ``SubscriptionRegistry`` is shared across all connections;
each WebSocket connection runs its own ``WSSession`` over a thin adapter from
Starlette's ``WebSocket`` to the ``WSTransport`` protocol.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from aegis import transcript_constants as _tc
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSDisconnect, WSSession

RESUME_GAP_CAP = 1000


def _constants() -> dict:
    return {
        "N_MAX": _tc.N_MAX,
        "EVICT_BATCH": _tc.EVICT_BATCH,
        "LOAD_BATCH": _tc.LOAD_BATCH,
        "STICKY_EPS": _tc.STICKY_EPS,
        "LOAD_MORE_EPS": _tc.LOAD_MORE_EPS,
        "DEBOUNCE_S": _tc.DEBOUNCE_S,
        "RESUME_GAP_CAP": RESUME_GAP_CAP,
    }


class _StarletteTransport:
    """Adapts Starlette's ``WebSocket`` to the ``WSTransport`` protocol,
    translating ``WebSocketDisconnect`` into the session-layer
    ``WSDisconnect``."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send_json(self, obj: dict) -> None:
        await self._ws.send_json(obj)

    async def receive_json(self) -> dict:
        try:
            return await self._ws.receive_json()
        except WebSocketDisconnect as exc:
            raise WSDisconnect() from exc

    async def close(self, code: int = 1000, reason: str = "") -> None:
        with contextlib.suppress(Exception):
            await self._ws.close(code=code)


def build_web_app(manager, web_cfg, state_dir, *,
                  static_dir: Path | None = None,
                  server_version: str = "0") -> Starlette:
    registry = SubscriptionRegistry(manager, Path(state_dir))
    constants = _constants()

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        transport = _StarletteTransport(ws)
        session = WSSession(transport, manager, registry, web_cfg,
                            constants, server_version=server_version)
        await session.run()

    routes = [
        Route("/healthz", healthz),
        WebSocketRoute("/ws", ws_endpoint),
    ]
    if static_dir is not None:
        routes.append(
            Mount("/static", StaticFiles(directory=str(static_dir)),
                  name="static"))
    return Starlette(routes=routes)
