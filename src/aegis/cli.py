"""``aegis serve``: the only module that reads the process's working directory.

It builds the roots from it once and passes them down (DESIGN.md, "Three
roots, never Path.cwd()").
"""

from __future__ import annotations

import errno
import socket
from pathlib import Path

import typer

HELP = "aegis: a web-native workplace for coding agents."

app = typer.Typer(add_completion=False, help=HELP)


def _version(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"aegis {version('aegis-harness')}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """aegis: a web-native workplace for coding agents."""


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
    log_level: str = typer.Option(
        "warning", help="debug, info, warning or error; info logs every operation."
    ),
) -> None:
    """Serve Claude Code sessions to browser tabs."""
    import logging

    import uvicorn

    logging.basicConfig(
        level=log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    from .app import App
    from .roots import make_roots
    from .web import build_web, load_or_create_token

    from .roots import legacy_state

    roots = make_roots(start=Path.cwd(), root=root)
    if found := legacy_state(roots.state_root):
        typer.echo(
            f"{roots.state_root} holds the state of aegis before 2.0 ({', '.join(found)}).\n"
            f"Move it aside, then start again:\n"
            f"  mv {roots.state_root} {roots.state_root.parent / 'legacy-state'}",
            err=True,
        )
        raise typer.Exit(1)
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
    import socket as _socket

    local = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    app = App(
        roots,
        claude_bin=claude,
        base_url=f"http://{local}:{port}",
        server_name=_socket.gethostname(),
    )
    web = build_web(app, token, allowed)
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    typer.echo(f"aegis serving {roots.config_root}")
    typer.echo(f"open http://{shown}:{port}/?token={token}")
    uvicorn.run(web, host=host, port=port, log_level="warning")


def main() -> None:
    app()
