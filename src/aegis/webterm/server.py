"""Running `aegis web`: its port, its uvicorn, and its way to the daemon."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import uvicorn

from aegis.daemon.lifecycle import ensure_daemon
from aegis.webterm.relay import Connect


def resolve_port(web_cfg, state_dir: Path) -> int:
    """The configured port, else the one recorded last time, else a free one,
    recorded. Never written back into `.aegis.yaml`: pinning it there is
    what made every daemon for a root bind it."""
    if web_cfg.port is not None:
        return int(web_cfg.port)
    persisted = Path(state_dir) / "web.port"
    if persisted.exists():
        try:
            return int(persisted.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text(str(port), encoding="utf-8")
    return port


def uvicorn_config(app, *, bind: str, port: int) -> uvicorn.Config:
    # access_log off: the one-time login URL carries the token.
    return uvicorn.Config(
        app, host=bind, port=port, log_level="warning", access_log=False
    )


def connect_for(root: Path, *, preflight=None) -> Connect:
    """Each connection finds (or starts) the daemon the way a terminal does,
    so a restarted daemon is found again and a stopped one is started."""

    async def connect():
        path = await ensure_daemon(Path(root), preflight=preflight)
        return await asyncio.open_unix_connection(str(path))

    return connect


async def run_web(app, *, bind: str, port: int) -> None:
    await uvicorn.Server(uvicorn_config(app, bind=bind, port=port)).serve()
