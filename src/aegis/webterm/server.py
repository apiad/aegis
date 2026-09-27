"""Running `aegis web`: its port, its uvicorn, and its way to the daemon."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import uvicorn

from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import ensure_daemon, socket_path
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


def connect_for(root: Path, *, preflight=None, autostart: bool = True) -> Connect:
    """Each connection finds (or starts) the daemon the way a terminal does,
    so a restarted daemon is found again and a stopped one is started.

    With ``autostart=False`` it only ever connects. systemd owns the daemon
    under `aegis-server.service`, and a web process that spawned its own
    would race that unit for the root's lock on boot.
    """

    async def connect():
        if autostart:
            path = await ensure_daemon(Path(root), preflight=preflight)
        else:
            path = socket_path(AegisRoots.for_project(Path(root)))
            if not path.exists():
                raise ConnectionError(
                    f"no daemon socket at {path} and --no-autostart is set; "
                    "start aegis-server.service first"
                )
        return await asyncio.open_unix_connection(str(path))

    return connect


async def run_web(app, *, bind: str, port: int) -> None:
    await uvicorn.Server(uvicorn_config(app, bind=bind, port=port)).serve()
