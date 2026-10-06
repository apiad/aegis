"""``aegis2 serve``: the only module that reads the process's working directory.

It builds the roots from it once and passes them down (DESIGN.md, "Three
roots, never Path.cwd()").
"""

from __future__ import annotations

import errno
import socket
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="aegis2, the web-native successor of aegis (experimental).",
)


@app.callback()
def _root() -> None:
    """aegis2, the web-native successor of aegis (experimental)."""


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                return False
            raise
    return True


@app.command()
def serve(
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
    port: int = typer.Option(8742, help="Port to listen on."),
    host: str = typer.Option(
        "127.0.0.1", help="Address to bind; anything but loopback must be asked for."
    ),
    claude: str = typer.Option("claude", help="The claude executable to run."),
) -> None:
    """Serve one Claude Code session to a browser tab."""
    import uvicorn

    from .app import App
    from .roots import make_roots
    from .web import build_web, load_or_create_token

    roots = make_roots(start=Path.cwd(), root=root)
    if not _port_free(host, port):
        typer.echo(f"port {port} on {host} is taken; pass --port", err=True)
        raise typer.Exit(1)
    token = load_or_create_token(roots.state_root)
    allowed = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"[::1]:{port}",
        f"{host}:{port}",
    }
    web = build_web(App(roots, claude_bin=claude), token, allowed)
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    typer.echo(f"aegis2 serving {roots.config_root}")
    typer.echo(f"open http://{shown}:{port}/?token={token}")
    uvicorn.run(web, host=host, port=port, log_level="warning")


def main() -> None:
    app()
