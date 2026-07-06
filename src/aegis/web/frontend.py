"""WebFrontend — uvicorn lifecycle owner for the web client. Built by
``aegis serve`` when a ``web:`` block is configured; run as an asyncio task.
"""
from __future__ import annotations

import socket
from pathlib import Path

import uvicorn

from aegis.web.server import build_web_app


def _resolve_port(web_cfg, state_dir: Path) -> int:
    if web_cfg.port is not None:
        return int(web_cfg.port)
    persisted = state_dir / "web.port"
    if persisted.exists():
        try:
            return int(persisted.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text(str(port), encoding="utf-8")
    return port


class WebFrontend:
    def __init__(self, manager, web_cfg, *, state_dir: Path,
                 server_version: str = "0") -> None:
        self._cfg = web_cfg
        self._state_dir = Path(state_dir)
        self._port = _resolve_port(web_cfg, self._state_dir)
        self._app = build_web_app(manager, web_cfg, self._state_dir,
                                  files_root=Path.cwd(),
                                  server_version=server_version)
        self._server: uvicorn.Server | None = None

    @property
    def url(self) -> str:
        token = f"?t={self._cfg.token}" if self._cfg.token else ""
        return f"http://{self._cfg.bind}:{self._port}/{token}"

    async def run(self) -> None:
        config = uvicorn.Config(
            self._app, host=self._cfg.bind, port=self._port,
            log_level="info", access_log=False)
        self._server = uvicorn.Server(config)
        await self._server.serve()
