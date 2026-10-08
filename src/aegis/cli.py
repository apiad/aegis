"""``aegis serve``, and ``aegis`` alone, which is ``serve --window``.

This is the only module that reads the process's working directory.

It builds the roots from it once and passes them down (DESIGN.md, "Three
roots, never Path.cwd()").
"""

from __future__ import annotations

import errno
import socket
import threading
import time
from pathlib import Path

import typer

HELP = "aegis: a web-native workplace for coding agents."
DEFAULT_PORT = 8742

app = typer.Typer(add_completion=False, help=HELP)


def _version(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"aegis {version('aegis-harness')}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """aegis: a web-native workplace for coding agents.

    With no command, serves and opens the server in a browser app window
    (`aegis serve --window`); the browser comes from AEGIS_BROWSER if set."""
    if ctx.invoked_subcommand is None:
        import os

        serve(
            root=None,
            port=DEFAULT_PORT,
            host="127.0.0.1",
            claude="claude",
            log_level="warning",
            window=True,
            browser=os.environ.get("AEGIS_BROWSER"),
            origin=[],
            detach=False,
        )


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


def _open_when_listening(host: str, port: int, url: str, browser: str | None) -> None:
    """Open the window once the port accepts, from a thread: uvicorn.run blocks."""
    from .window import open_window

    def wait_then_open() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                socket.create_connection((host, port), timeout=0.2).close()
            except OSError:
                time.sleep(0.05)
                continue
            open_window(url, browser)
            return

    threading.Thread(target=wait_then_open, daemon=True).start()


def _listening(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=0.2).close()
        return True
    except OSError:
        return False


def _tail(path: Path, lines: int = 20) -> str:
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


DETACH_BOOT_S = 30


def _detach(
    roots,
    host: str,
    local: str,
    port: int,
    claude: str,
    log_level: str,
    origins: list[str],
    urls: list[str],
    opencode: str = "opencode",
) -> None:
    """Run this serve again, undetached, in its own session; return once it listens.

    A re-exec rather than a fork: the parent may already hold threads, and a
    forked asyncio process inherits them half-alive. Its own session keeps it
    clear of the terminal's hangup; its output goes to ``serve.log`` because
    nothing will read its stdout. The parent waits for the port so a boot
    failure is reported here, before the prompt comes back."""
    import subprocess
    import sys

    log_path = roots.state_root / "serve.log"
    pid_path = roots.state_root / "serve.pid"
    cmd = [
        sys.executable, "-m", "aegis", "serve",
        "--root", str(roots.config_root),
        "--port", str(port),
        "--host", host,
        "--claude", claude,
        "--opencode", opencode,
        "--log-level", log_level,
    ]  # fmt: skip
    for o in origins:
        cmd += ["--origin", o]
    with log_path.open("a") as log:
        log.write(f"== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} aegis serve --detach\n")
        log.flush()
        child = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=roots.config_root,
        )
    pid_path.write_text(f"{child.pid}\n")
    deadline = time.monotonic() + DETACH_BOOT_S
    while not _listening(local, port):
        if child.poll() is not None:
            pid_path.unlink(missing_ok=True)
            typer.echo(
                f"aegis serve exited with {child.returncode} before listening; "
                f"the end of {log_path}:\n{_tail(log_path)}",
                err=True,
            )
            raise typer.Exit(1)
        if time.monotonic() > deadline:
            typer.echo(
                f"aegis serve (pid {child.pid}) is not listening after "
                f"{DETACH_BOOT_S} s; see {log_path}",
                err=True,
            )
            raise typer.Exit(1)
        time.sleep(0.05)
    typer.echo(f"aegis serving {roots.config_root} in the background (pid {child.pid})")
    for url in urls:
        typer.echo(f"open {url}")
    typer.echo(f"log {log_path}; stop it with: kill {child.pid}")


def _origins(values: list[str]) -> list[str]:
    from .web import public_origin

    try:
        return [public_origin(v) for v in values]
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


@app.command()
def serve(
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
    port: int = typer.Option(DEFAULT_PORT, help="Port to listen on."),
    host: str = typer.Option(
        "127.0.0.1", help="Address to bind; anything but loopback must be asked for."
    ),
    claude: str = typer.Option("claude", help="The claude executable to run."),
    opencode: str = typer.Option("opencode", help="The opencode executable to run."),
    log_level: str = typer.Option(
        "warning", help="debug, info, warning or error; info logs every operation."
    ),
    window: bool = typer.Option(
        False,
        "--window",
        help="Open the server in a browser app window; if this root's server is "
        "already running, just open the window.",
    ),
    browser: str | None = typer.Option(
        None,
        envvar="AEGIS_BROWSER",
        help="The browser for --window; default: the first Chrome, Chromium, "
        "Edge or Brave installed, else the system browser.",
    ),
    origin: list[str] = typer.Option(
        [],
        "--origin",
        callback=_origins,
        help="A public origin a reverse proxy serves aegis at, e.g. "
        "https://dev.example; repeatable. Its browsers' sockets are accepted.",
    ),
    detach: bool = typer.Option(
        False,
        "--detach",
        "-d",
        help="Start in the background and return once it listens; output goes "
        "to <state>/serve.log, the pid to <state>/serve.pid.",
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
    local = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    if not _port_free(host, port):
        if window and _open_running(roots.state_root, local, port, browser):
            typer.echo(f"aegis already serving {roots.config_root}; opened a window")
            return
        typer.echo(f"port {port} on {host} is taken; pass --port", err=True)
        raise typer.Exit(1)
    token = load_or_create_token(roots.state_root)
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    url = f"http://{shown}:{port}/?token={token}"
    if detach:
        urls = [url, *(f"{o}/?token={token}" for o in origin)]
        _detach(roots, host, local, port, claude, log_level, origin, urls, opencode)
        if window:
            from .window import open_window

            open_window(url, browser)
        return
    allowed = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"[::1]:{port}",
        f"{host}:{port}",
    }
    import socket as _socket

    app = App(
        roots,
        claude_bin=claude,
        opencode_bin=opencode,
        base_url=f"http://{local}:{port}",
        server_name=_socket.gethostname(),
    )
    web = build_web(app, token, allowed, origin)
    typer.echo(f"aegis serving {roots.config_root}")
    typer.echo(f"open {url}")
    for o in origin:
        typer.echo(f"open {o}/?token={token}")
    if window:
        _open_when_listening(local, port, url, browser)
    uvicorn.run(web, host=host, port=port, log_level="warning")


def _open_running(state_root: Path, host: str, port: int, browser: str | None) -> bool:
    """Open a window on this root's running server; False if the port holds
    anything else."""
    from .window import open_window, serves

    token_file = state_root / "token"
    if not token_file.is_file():
        return False
    token = token_file.read_text().strip()
    if not serves(host, port, token):
        return False
    open_window(f"http://{host}:{port}/?token={token}", browser)
    return True


def main() -> None:
    app()
