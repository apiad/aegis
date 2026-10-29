"""``aegis serve``, ``aegis init``, ``aegis doctor``, ``aegis link``, ``aegis service``, ``aegis usage``,
``aegis journal``, ``aegis map``, ``aegis call``, ``aegis tools``, and ``aegis`` alone, which is ``serve --window``.

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

from wayfind.cli import app as map_app  # noqa: E402
from .journal.cli import app as journal_app  # noqa: E402
from .usage.cli import app as usage_app  # noqa: E402

app.add_typer(journal_app, name="journal")
app.add_typer(usage_app, name="usage", help="What the agents' work cost.")
app.add_typer(map_app, name="map")


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
            codex="codex",
            cursor="cursor-agent",
            log_level="warning",
            window=True,
            browser=os.environ.get("AEGIS_BROWSER"),
            origin=[],
            detach=False,
            name=None,
            paused=False,
        )


def roots_here(root: Path | None):
    """The roots for a command run here, for the subcommand groups (usage)."""
    from .roots import make_roots

    return make_roots(start=Path.cwd(), root=root)


def path_here(path: str) -> str:
    """A path typed at this shell, absolute: a relative one is read from the
    shell's directory, as a person means it, not from the config root."""
    return str(Path.cwd() / Path(path).expanduser())


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
    codex: str = "codex",
    cursor: str = "cursor-agent",
) -> None:
    """Run this serve again, undetached, in its own session; return once it listens.

    A re-exec rather than a fork: the parent may already hold threads, and a
    forked asyncio process inherits them half-alive. Its own session keeps it
    clear of the terminal's hangup; its output goes to ``serve.log`` because
    nothing will read its stdout. The parent waits for the port so a boot
    failure is reported here, before the prompt comes back."""
    import subprocess

    from . import launch
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
        "--codex", codex,
        "--cursor", cursor,
        "--log-level", log_level,
    ]  # fmt: skip
    for o in origins:
        cmd += ["--origin", o]
    if name:
        cmd += ["--name", name]
    with log_path.open("a") as log:
        log.write(f"== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} aegis serve --detach\n")
        log.flush()
        child = launch.detach(
            cmd, cwd=roots.config_root, stdout=log, stderr=subprocess.STDOUT, user=None
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
    codex: str = typer.Option("codex", help="The codex executable to run."),
    cursor: str = typer.Option(
        "cursor-agent", help="The cursor-agent executable to run."
    ),
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
    paused: bool = typer.Option(
        False,
        "--paused",
        help="Start with every session paused: cut turns stay cut and nothing "
        "runs until you resume it (Settings, or a message to one session).",
    ),
) -> None:
    """Serve Claude Code sessions to browser tabs."""
    import logging

    import uvicorn

    logging.basicConfig(
        level=log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    from .app import App
    from .server import StartError, allowed_hosts, prepare
    from .web import build_web, load_or_create_token, ws_protocol

    try:
        prepared = prepare(Path.cwd(), root)
    except StartError as e:
        for line in e.lines:
            typer.echo(line, err=True)
        raise typer.Exit(1)
    roots = prepared.roots
    local = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    if not _port_free(host, port):
        if window and _open_running(roots.state_root, local, port, browser):
            typer.echo(f"aegis already serving {roots.config_root}; opened a window")
            return
        typer.echo(f"port {port} on {host} is taken; pass --port", err=True)
        raise typer.Exit(1)
    if paused:
        from . import pause

        pause.write(roots.state_root, by="aegis serve --paused")
    token = load_or_create_token(roots.state_root)
    shown = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host
    url = f"http://{shown}:{port}/?token={token}"
    if detach:
        urls = [url, *(f"{o}/?token={token}" for o in origin)]
        _detach(
            roots,
            host,
            local,
            port,
            claude,
            log_level,
            origin,
            urls,
            opencode,
            name,
            codex,
            cursor,
        )
        if window:
            from .window import open_window

            open_window(url, browser)
        return
    allowed = allowed_hosts(host, port)
    import socket as _socket

    app = App(
        roots,
        claude_bin=claude,
        opencode_bin=opencode,
        codex_bin=codex,
        cursor_bin=cursor,
        base_url=f"http://{local}:{port}",
        server_name=name or _socket.gethostname(),
        multi_user=prepared.multi_user,
    )
    web = build_web(app, token, allowed, origin)
    typer.echo(f"aegis serving {roots.config_root}")
    typer.echo(f"open {url}")
    for o in origin:
        typer.echo(f"open {o}/?token={token}")
    if window:
        _open_when_listening(local, port, url, browser)
    uvicorn.run(web, host=host, port=port, log_level="warning", ws=ws_protocol())


def _bins(claude: str, opencode: str, codex: str, cursor: str) -> dict[str, str]:
    return {
        "claude-code": claude,
        "opencode": opencode,
        "codex": codex,
        "cursor": cursor,
    }


def _report(findings) -> int:
    """Print the findings; the number of errors."""
    from .doctor import render

    typer.echo(render(findings))
    return sum(f.level == "error" for f in findings)


@app.command()
def doctor(
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
    claude: str = typer.Option("claude", help="The claude executable to check."),
    opencode: str = typer.Option("opencode", help="The opencode executable to check."),
    codex: str = typer.Option("codex", help="The codex executable to check."),
    cursor: str = typer.Option(
        "cursor-agent", help="The cursor-agent executable to check."
    ),
) -> None:
    """Check .aegis.yaml, the harnesses it names and the state directory."""
    from .doctor import doctor as run_doctor
    from .roots import make_roots
    from .service import check as service_check

    start = Path.cwd()
    roots = make_roots(start=start, root=root)
    findings = asyncio.run(
        run_doctor(
            roots, _bins(claude, opencode, codex, cursor), start=None if root else start
        )
    )
    findings += service_check()
    raise typer.Exit(1 if _report(findings) else 0)


@app.command("import-legacy")
def import_legacy(
    source: Path = typer.Argument(
        ..., help="The legacy tree's state directory, the one holding sessions/."
    ),
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
) -> None:
    """Copy the sessions of aegis before 2.0 into the archive, to read.

    Sessions already imported are skipped, so it is safe to run again. A running
    server lists the new ones after a restart."""
    from .legacy_import import import_legacy as run
    from .roots import legacy_state, make_roots

    roots = make_roots(start=Path.cwd(), root=root)
    if not (source / "sessions").is_dir():
        typer.echo(f"no sessions/ in {source}: not a legacy state directory")
        raise typer.Exit(2)
    if found := legacy_state(roots.state_root):
        typer.echo(
            f"{roots.state_root} holds legacy state ({', '.join(found)}); "
            "move it aside and import from there"
        )
        raise typer.Exit(2)
    r = run(source, roots.state_root)
    typer.echo(
        f"{r.imported} imported, {r.already} already imported, "
        f"{r.empty} empty skipped, {r.damaged_lines} damaged lines skipped"
    )


def _pick(text: str, default: str | None, choices) -> str:
    """Ask until the answer is one of ``choices``. Typer 0.27 vendors its own
    click, so a click.Choice from the click package escaped its prompt as a
    traceback instead of asking again."""
    while True:
        answer = typer.prompt(f"{text} ({', '.join(choices)})", default=default)
        if answer in choices:
            return answer
        typer.echo(f"{answer!r} is not one of {', '.join(choices)}")


def _count(text: str, default: int, least: int = 1) -> int:
    """Ask until the answer is a whole number of at least ``least``."""
    while True:
        answer = typer.prompt(text, default=str(default))
        if answer.strip().isdigit() and int(answer) >= least:
            return int(answer)
        typer.echo(f"{answer!r} is not a whole number of at least {least}")


def _ask(doc, found):
    """``doc`` as the person answers for it, each value offered as the default."""
    from .agents import EFFORTS, PERMISSION_ORDER
    from .config import ConfigDoc, QueueDoc

    agents = []
    for a in doc.agents:
        if not typer.confirm(f"Add agent {a.name!r} ({a.harness})?", default=True):
            continue
        models = next((f.models for f in found if f.harness == a.harness), ())
        if models:
            typer.echo("  models: " + ", ".join(m.value for m in models[:12]))
        if a.models:
            # A cascade is asked as one list, best first.
            typed = typer.prompt("  models", default=", ".join(a.models))
            listed = [x.strip() for x in typed.split(",") if x.strip()] or a.models
            update = {"model": listed[0], "models": listed if len(listed) > 1 else []}
        else:
            update = {"model": typer.prompt("  model", default=a.model)}
        agents.append(
            a.model_copy(
                update={
                    **update,
                    "effort": _pick("  effort", a.effort, EFFORTS),
                    "permission": _pick("  permission", a.permission, PERMISSION_ORDER),
                }
            )
        )
    if not agents:
        return ConfigDoc()
    names = [a.name for a in agents]
    first = doc.default_agent if doc.default_agent in names else names[0]
    default = _pick("Default agent", first, names)
    queues = []
    if typer.confirm(
        f"Add a queue 'general' of workers running {default}?", default=True
    ):
        n = _count("  workers at a time", 3)
        queues.append(QueueDoc(name="general", agent=default, max_parallel=n))
    for q in doc.queues:
        if q.name == "general" or q.agent not in names or q.agent == default:
            continue
        if typer.confirm(
            f"Add a queue {q.name!r} of workers running {q.agent}?", default=True
        ):
            n = _count("  workers at a time", q.max_parallel or 3)
            queues.append(QueueDoc(name=q.name, agent=q.agent, max_parallel=n))
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
    codex: str = typer.Option("codex", help="The codex executable to look for."),
    cursor: str = typer.Option(
        "cursor-agent", help="The cursor-agent executable to look for."
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
    bins = _bins(claude, opencode, codex, cursor)
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
    "can never reach it, unless the link is trusted (--trusted).",
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
    trusted: bool = typer.Option(
        False,
        "--trusted",
        help="Both ways, agents unrestricted: only for a server you alone use.",
    ),
) -> None:
    """Link the server at URL. Reads its token from stdin, so it stays out of
    the shell history: `ssh far cat .aegis/state/token | aegis link add far URL`.
    With --trusted, the far server also reaches this one over the link, and
    agents on both act across it as at home; it grants that only to its admin
    token while nobody else holds one."""
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
        welcome = asyncio.run(probe(url, token, own, getpass.getuser(), trusted))
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
        | ({"trusted": True} if trusted else {})
    )
    store.save(entries)
    how = "trusted, both ways" if trusted else "one way"
    typer.echo(
        f"linked {name} at {url}, {how}; a running server picks it up within a second"
    )


@link_app.command("trust")
def link_trust(
    name: str = typer.Argument(...),
    off: bool = typer.Option(False, "--off", help="Stop asking for trust."),
    root: Path | None = _ROOT_OPTION,
) -> None:
    """Ask an existing link for trust (both ways, agents unrestricted), or stop
    with --off. The link reconnects; the far server decides, and Settings
    shows whether it granted it."""
    store = _link_store(root)
    entries = store.load()
    if not any(e["name"] == name for e in entries):
        typer.echo(f"no link named {name}", err=True)
        raise typer.Exit(1)
    for e in entries:
        if e["name"] == name:
            e.pop("trusted", None)
            if not off:
                e["trusted"] = True
    store.save(entries)
    typer.echo(f"{name}: {'one way' if off else 'trust asked'}")


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
        trust = "  trusted" if e.get("trusted") is True else ""
        typer.echo(f"{e['name']:<16} {e['url']}{trust}")
    typer.echo(f"{len(entries)} links")


service_app = typer.Typer(
    add_completion=False, help="Run aegis under systemd, with the recommended unit."
)
app.add_typer(service_app, name="service")


def _ask_or(value, label: str, default, yes: bool):
    if value is not None:
        return value
    return default if yes else typer.prompt(label, default=default)


@service_app.command("install")
def service_install(
    user: str | None = typer.Option(None, help="The user the server runs as."),
    port: int | None = typer.Option(
        None, help=f"The port it serves on ({DEFAULT_PORT})."
    ),
    root: Path | None = typer.Option(
        None, help="The directory it serves; default: here."
    ),
    memory_high: str | None = typer.Option(
        None, help="MemoryHigh; default: about 70% of this host's RAM."
    ),
    memory_max: str | None = typer.Option(
        None, help="MemoryMax; default: about 80% of this host's RAM."
    ),
    aegis: str | None = typer.Option(
        None, help="The aegis executable; default: the one on PATH."
    ),
    output: Path = typer.Option(Path("aegis.service"), help="Where to write the unit."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the unit and write nothing."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Take every default without asking."
    ),
    multi_user: bool = typer.Option(
        False,
        "--multi-user",
        help="Give the server the capabilities multi_user in .aegis.yaml needs.",
    ),
) -> None:
    """Write a systemd unit for `aegis serve` and print the commands that
    install it. Never runs sudo."""
    import getpass
    import os
    import shutil
    import sys

    from . import service
    from .service import Unit, caps, mem_total, parse_size, render

    total = mem_total(service.MEMINFO.read_text())
    high, top = caps(total)
    exe = shutil.which("aegis") or str(Path(sys.argv[0]).resolve())
    unit = Unit(
        user=_ask_or(user, "User", getpass.getuser(), yes),
        root=Path(_ask_or(root, "Root directory", str(Path.cwd()), yes)).resolve(),
        port=int(_ask_or(port, "Port", DEFAULT_PORT, yes)),
        aegis=_ask_or(aegis, "aegis executable", exe, yes),
        path=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        memory_high=_ask_or(memory_high, "MemoryHigh", high, yes),
        memory_max=_ask_or(memory_max, "MemoryMax", top, yes),
        multi_user=multi_user,
    )
    if multi_user and unit.user == "root":
        typer.echo(
            "multi-user mode runs the server as an unprivileged user, never root",
            err=True,
        )
        raise typer.Exit(1)
    for key, v in (("MemoryHigh", unit.memory_high), ("MemoryMax", unit.memory_max)):
        if parse_size(v, total) is None:
            typer.echo(f"{key}={v} is not a size systemd reads, such as 5.5G", err=True)
            raise typer.Exit(1)
    text = render(unit)
    if dry_run:
        typer.echo(text, nl=False)
        return
    output.write_text(text)
    path = output.resolve()
    typer.echo(f"wrote {path}. To install and start it:\n")
    typer.echo(f"  sudo install -m 644 {path} /etc/systemd/system/{path.name}")
    typer.echo("  sudo systemctl daemon-reload")
    typer.echo(f"  sudo systemctl enable --now {path.name}")


token_app = typer.Typer(
    add_completion=False,
    help="The tokens that may use this server: one per person, linked server "
    "or job. A running server applies a change at its next request.",
)
app.add_typer(token_app, name="token")


def _token_store(root: Path | None):
    import getpass

    from .config import Config
    from .roots import make_roots
    from .tokens import TokenStore

    roots = make_roots(start=Path.cwd(), root=root)
    return TokenStore(
        roots.state_root,
        getpass.getuser(),
        multi_user=Config(roots.config_root).current().multi_user is not None,
    )


@token_app.command("add")
def token_add(
    name: str = typer.Argument(..., help="Who it is: lowercase, e.g. ben or laptop."),
    role: str = typer.Option("member", help="member or admin."),
    linux_user: str | None = typer.Option(
        None,
        "--linux-user",
        help="The Linux user it runs as, in multi-user mode; default: NAME.",
    ),
    no_linux_user: bool = typer.Option(
        False,
        "--no-linux-user",
        help="An admin that runs as the server's own user, in multi-user mode.",
    ),
    root: Path | None = _ROOT_OPTION,
) -> None:
    """Mint a token and print it, alone on stdout: the only time it is shown."""
    from .ops import OpError

    try:
        token = _token_store(root).add(name, role, linux_user, no_linux_user)
    except OpError as e:
        typer.echo(e.message, err=True)
        raise typer.Exit(1) from e
    typer.echo(token)


@token_app.command("list")
def token_list(root: Path | None = _ROOT_OPTION) -> None:
    """Names, roles and quota policies, never tokens."""
    from .quota_gate import describe

    store = _token_store(root)
    rows = store.rows()
    for r in rows:
        own = "" if r["policy"] is not None or r["role"] == "admin" else " (default)"
        user = f"{r['linux_user'] or '-':<16} " if store.multi_user else ""
        typer.echo(
            f"{r['name']:<16} {r['role']:<7} {user}{describe(store.policy_of(r['name']))}{own}"
            f"{'  (this server)' if r['owner'] else ''}"
        )
    typer.echo(f"{len(rows)} tokens")


@token_app.command("revoke")
def token_revoke(
    name: str = typer.Argument(..., help="Whose token to end."),
    root: Path | None = _ROOT_OPTION,
) -> None:
    """End a token. Revoking the admin's prints its replacement."""
    from .ops import OpError

    try:
        new = _token_store(root).revoke(name)
    except OpError as e:
        typer.echo(e.message, err=True)
        raise typer.Exit(1) from e
    typer.echo(new if new else f"revoked {name}")


@app.command("call")
def call(
    tool: str = typer.Argument(..., help="The tool, e.g. session_list."),
    args: list[str] = typer.Argument(
        None,
        help="key=value pairs (a value is read as JSON when it parses), "
        "or one JSON object; `-` reads the JSON object from stdin.",
    ),
) -> None:
    """Call one aegis tool as this session; prints its result."""
    from .call import call as run

    run(tool, args or [])


@app.command("tools")
def tools(
    name: str | None = typer.Argument(None, help="Show this tool's parameters."),
) -> None:
    """List the aegis tools, or one tool's description and JSON schema."""
    from .call import tools as run

    run(name)


def main() -> None:
    app()
