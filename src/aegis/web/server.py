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
from starlette.responses import (
    FileResponse, HTMLResponse, JSONResponse, Response)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from aegis import transcript_constants as _tc
from aegis.themes import list_theme_names, load_theme
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSDisconnect, WSSession

RESUME_GAP_CAP = 1000
_PKG_STATIC = Path(__file__).resolve().parent / "static"
WEB_THEME = "aegis-ink"


def _constants() -> dict:
    return {
        "N_MAX": _tc.N_MAX,
        "REPLAY_TAIL": _tc.REPLAY_TAIL,
        "EVICT_BATCH": _tc.EVICT_BATCH,
        "LOAD_BATCH": _tc.LOAD_BATCH,
        "STICKY_EPS": _tc.STICKY_EPS,
        "LOAD_MORE_EPS": _tc.LOAD_MORE_EPS,
        "DEBOUNCE_S": _tc.DEBOUNCE_S,
        "RESUME_GAP_CAP": RESUME_GAP_CAP,
        "TOOL_RESULT_HEAD_LINES": _tc.TOOL_RESULT_HEAD_LINES,
        "TOOL_INPUT_HEAD_LINES": _tc.TOOL_INPUT_HEAD_LINES,
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
                  files_root: Path | None = None,
                  server_version: str = "0") -> Starlette:
    registry = SubscriptionRegistry(manager, Path(state_dir))
    constants = _constants()

    # Queue monitoring: build a digest over the attached QueueManager (the TUI
    # does the same; aegis serve doesn't otherwise). Each queue event triggers
    # a digest broadcast to subscribed web clients.
    qm = getattr(manager, "queue_manager", None)
    if qm is not None:
        from aegis.queue import QueueDigest
        digest = QueueDigest(qm)
        digest.start()
        registry.set_digest(digest)
        qm.subscribe(lambda ev: registry.broadcast_queue_digest())

    # File picker + viewer: index the served project tree.
    if files_root is not None:
        from aegis.tui.file_index import FileIndexer
        indexer = FileIndexer()
        indexer.start(Path(files_root))
        registry.set_files(indexer, Path(files_root).resolve())
    static = Path(static_dir) if static_dir is not None else _PKG_STATIC
    index_html = (static / "index.html").read_text(encoding="utf-8")
    base_css = (static / "css" / "base.css").read_text(encoding="utf-8")
    manifest_json = (static / "manifest.webmanifest").read_text(encoding="utf-8")
    sw_src = (static / "service-worker.js").read_text(encoding="utf-8")

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def index(request):
        return HTMLResponse(index_html)

    async def download(request):
        if request.query_params.get("t") != web_cfg.token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if files_root is None:
            return JSONResponse({"error": "files unavailable"}, status_code=404)
        root = Path(files_root).resolve()
        target = (root / request.query_params.get("path", "")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return JSONResponse({"error": "path outside project"},
                                status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "not a file"}, status_code=404)
        return FileResponse(target, filename=target.name)

    async def manifest(request):
        return Response(manifest_json, media_type="application/manifest+json")

    async def service_worker(request):
        body = sw_src.replace("__SW_VERSION__", server_version)
        return Response(body, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/",
                                 "Cache-Control": "no-cache"})

    async def theme_css(request):
        name = request.query_params.get("name") or WEB_THEME
        if name not in list_theme_names():
            name = WEB_THEME
        css = load_theme(name).to_css_variables() + "\n" + base_css
        return Response(css, media_type="text/css")

    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        transport = _StarletteTransport(ws)
        session = WSSession(transport, manager, registry, web_cfg,
                            constants, server_version=server_version)
        await session.run()

    routes = [
        Route("/", index),
        Route("/download", download),
        Route("/healthz", healthz),
        Route("/manifest.webmanifest", manifest),
        Route("/service-worker.js", service_worker),
        Route("/theme.css", theme_css),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/static", StaticFiles(directory=str(static)), name="static"),
    ]
    return Starlette(routes=routes)
