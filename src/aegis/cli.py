"""``aegis serve``, ``aegis init``, ``aegis doctor``, ``aegis link``, and ``aegis``
alone, which is ``serve --window``.

This is the only module that reads the process's working directory.

It builds the roots from it once and passes them down (DESIGN.md, "Three
roots, never Path.cwd()").
"""

from __future__ import annotations

import asyncio
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
            opencode="opencode",
            log_level="warning",
            window=True,
            browser=os.environ.get("AEGIS_BROWSER"),
            origin=[],
            detach=False,
            name=None,
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
    name: str | None = None,
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
    if name:
        cmd += ["--name", name]
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
    name: str | None = typer.Option(
        None,
        "--name",
        help="This server's name, the @server in addresses; default: the hostname.",
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
        _detach(
            roots, host, local, port, claude, log_level, origin, urls, opencode, name
        )
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
        server_name=name or _socket.gethostname(),
    )
    web = build_web(app, token, allowed, origin)
    typer.echo(f"aegis serving {roots.config_root}")
    typer.echo(f"open {url}")
    for o in origin:
        typer.echo(f"open {o}/?token={token}")
    if window:
        _open_when_listening(local, port, url, browser)
    uvicorn.run(web, host=host, port=port, log_level="warning")


MARK = {"ok": "ok   ", "warn": "warn ", "error": "ERROR"}


def _bins(claude: str, opencode: str) -> dict[str, str]:
    return {"claude-code": claude, "opencode": opencode}


def _report(findings) -> int:
    """Print the findings; the number of errors."""
    for f in findings:
        typer.echo(f"{MARK[f.level]} {f.where:<26} {f.message}")
    errors = sum(f.level == "error" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    typer.echo(f"{errors} errors, {warns} warnings")
    return errors


@app.command()
def doctor(
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
    claude: str = typer.Option("claude", help="The claude executable to check."),
    opencode: str = typer.Option("opencode", help="The opencode executable to check."),
) -> None:
    """Check .aegis.yaml, the harnesses it names and the state directory."""
    from .doctor import doctor as run_doctor
    from .roots import make_roots

    start = Path.cwd()
    roots = make_roots(start=start, root=root)
    findings = asyncio.run(
        run_doctor(roots, _bins(claude, opencode), start=None if root else start)
    )
    raise typer.Exit(1 if _report(findings) else 0)


def _ask(doc, found):
    """``doc`` as the person answers for it, each value offered as the default."""
    import click

    from .agents import EFFORTS, PERMISSION_ORDER
    from .config import ConfigDoc, QueueDoc

    agents = []
    for a in doc.agents:
        if not typer.confirm(f"Add agent {a.name!r} ({a.harness})?", default=True):
            continue
        models = next((f.models for f in found if f.harness == a.harness), ())
        if models:
            typer.echo("  models: " + ", ".join(m.value for m in models[:12]))
        agents.append(
            a.model_copy(
                update={
                    "model": typer.prompt("  model", default=a.model),
                    "effort": typer.prompt(
                        "  effort", default=a.effort, type=click.Choice(EFFORTS)
                    ),
                    "permission": typer.prompt(
                        "  permission",
                        default=a.permission,
                        type=click.Choice(PERMISSION_ORDER),
                    ),
                }
            )
        )
    if not agents:
        return ConfigDoc()
    names = [a.name for a in agents]
    first = doc.default_agent if doc.default_agent in names else names[0]
    default = typer.prompt("Default agent", default=first, type=click.Choice(names))
    queues = []
    if typer.confirm(
        f"Add a queue 'general' of workers running {default}?", default=True
    ):
        n = typer.prompt("  workers at a time", default=3, type=click.IntRange(1))
        queues.append(QueueDoc(name="general", agent=default, max_parallel=n))
    return ConfigDoc(agents=agents, default_agent=default, queues=queues)


@app.command()
def init(
    root: Path | None = typer.Option(
        None, help="Where to write .aegis.yaml; default: here."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept every proposal without asking."
    ),
    claude: str = typer.Option("claude", help="The claude executable to look for."),
    opencode: str = typer.Option(
        "opencode", help="The opencode executable to look for."
    ),
) -> None:
    """Write a first .aegis.yaml from the harnesses installed here."""
    from .config import write
    from .doctor import LABELS, detect, propose
    from .doctor import doctor as run_doctor
    from .roots import CONFIG_FILE, find_config_root, make_roots

    target = (root or Path.cwd()).resolve()
    path = target / CONFIG_FILE
    if not target.is_dir():
        typer.echo(f"{target} is not a directory", err=True)
        raise typer.Exit(1)
    if path.exists():
        typer.echo(f"{path} already exists; run `aegis doctor` to check it", err=True)
        raise typer.Exit(1)
    parent = find_config_root(target.parent) / CONFIG_FILE
    if parent.is_file():
        typer.echo(f"{parent} governs {target} now.")
        if not yes and not typer.confirm(
            f"Create a new aegis root at {target}?", default=True
        ):
            raise typer.Exit(1)
    bins = _bins(claude, opencode)
    found = asyncio.run(detect(target, bins))
    for f in found:
        if f.bin is None:
            typer.echo(f"{LABELS[f.harness]}: not found ({f.error})")
        elif f.error:
            typer.echo(f"{LABELS[f.harness]}: {f.bin}, {f.error}")
        else:
            typer.echo(
                f"{LABELS[f.harness]}: {f.bin}, {f.version}, {len(f.models)} models"
            )
    if not any(f.bin for f in found):
        typer.echo(
            "No harness found. Install Claude Code or OpenCode, then run `aegis init` again.",
            err=True,
        )
        raise typer.Exit(1)
    doc = propose(found)
    if not yes:
        doc = _ask(doc, found)
    if not doc.agents:
        typer.echo("No agents chosen; nothing written.", err=True)
        raise typer.Exit(1)
    if problems := write(path, doc, None):
        _report(problems)
        raise typer.Exit(1)
    typer.echo(f"\nwrote {path}:\n")
    typer.echo(path.read_text())
    errors = _report(asyncio.run(run_doctor(make_roots(target, target), bins)))
    raise typer.Exit(1 if errors else 0)


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


link_app = typer.Typer(
    add_completion=False,
    help="Link other aegis servers: this one becomes their client, and they "
    "can never reach it.",
)
app.add_typer(link_app, name="link")

_ROOT_OPTION = typer.Option(
    None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
)


def _link_store(root: Path | None):
    from .links import LinkStore
    from .roots import make_roots

    return LinkStore(make_roots(start=Path.cwd(), root=root).state_root / "links.json")


@link_app.command("add")
def link_add(
    name: str = typer.Argument(..., help="What the far server calls itself."),
    url: str = typer.Argument(..., help="Its page URL, e.g. https://dev.example"),
    root: Path | None = _ROOT_OPTION,
    server: str | None = typer.Option(
        None, "--as", help="This server's name, if serve runs with --name."
    ),
) -> None:
    """Link the server at URL. Reads its token from stdin, so it stays out of
    the shell history: `ssh far cat .aegis/state/token | aegis link add far URL`."""
    import getpass
    import sys

    from .links import Links, LinkError, probe
    from .ops import OpError

    token = sys.stdin.readline().strip()
    if not token:
        typer.echo("no token on stdin", err=True)
        raise typer.Exit(1)
    store = _link_store(root)
    own = server or socket.gethostname()
    try:
        welcome = asyncio.run(probe(url, token, own, getpass.getuser()))
    except LinkError as e:
        typer.echo(f"not linked: {e.message}", err=True)
        raise typer.Exit(1) from e
    if welcome.get("server") != name:
        typer.echo(
            f"not linked: that server calls itself {welcome.get('server')}, not {name}",
            err=True,
        )
        raise typer.Exit(1)
    links = Links(store.path.parent, own, getpass.getuser(), lambda *_: None)
    try:
        links.check(name, url)
    except OpError as e:
        typer.echo(f"not linked: {e.message}", err=True)
        raise typer.Exit(1) from e
    entries = store.load()
    entries.append(
        {
            "name": name,
            "url": url.rstrip("/"),
            "token": token,
            "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    store.save(entries)
    typer.echo(f"linked {name} at {url}; a running server picks it up within a second")


@link_app.command("remove")
def link_remove(
    name: str = typer.Argument(...), root: Path | None = _ROOT_OPTION
) -> None:
    """Forget a link."""
    store = _link_store(root)
    entries = store.load()
    if not any(e["name"] == name for e in entries):
        typer.echo(f"no link named {name}", err=True)
        raise typer.Exit(1)
    store.save([e for e in entries if e["name"] != name])
    typer.echo(f"removed {name}")


@link_app.command("list")
def link_list(root: Path | None = _ROOT_OPTION) -> None:
    """The links this server holds: names and URLs, never tokens."""
    entries = _link_store(root).load()
    for e in entries:
        typer.echo(f"{e['name']:<16} {e['url']}")
    typer.echo(f"{len(entries)} links")


def main() -> None:
    app()
