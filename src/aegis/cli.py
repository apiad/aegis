from __future__ import annotations

import asyncio
import signal
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import (
    ConfigError, find_project_root, load_config, load_queues,
    load_telegram_config,
)
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.tui import AegisApp

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = _pkg_version("aegis-harness")
        except PackageNotFoundError:        # not installed (rare in dev)
            v = "0.0.0+unknown"
        typer.echo(f"aegis {v}")
        raise typer.Exit()


@app.command()
def init(
    force: bool = typer.Option(
        False, "--force", "-f",
        help="Overwrite any existing .aegis.py and ignore upstream ones."),
) -> None:
    """Interactive wizard to create a .aegis.py.

    Detects installed agent CLIs (claude, gemini, opencode) and walks
    you through adding agents and queues. Refuses to run if any
    .aegis.py exists in the current dir or an ancestor (pass --force
    to override + overwrite).
    """
    upstream = find_project_root()
    target = (upstream or Path.cwd()) / ".aegis.py"

    if upstream is not None and not force:
        _console.print(
            f"[red]aegis already configured at "
            f"[bold]{upstream / '.aegis.py'}[/bold].[/red]\n"
            f"[dim]Pass --force to overwrite and re-run the wizard.[/dim]")
        raise typer.Exit(1)

    if target.exists() and not force:
        # find_project_root returned None (no upstream found from here)
        # but the file still exists in cwd. Same refuse-without-force.
        _console.print(
            f"[red]{target} already exists.[/red]\n"
            f"[dim]Pass --force to overwrite.[/dim]")
        raise typer.Exit(1)

    from aegis.init_wizard import render_aegis_py, run_wizard
    config = run_wizard(_console)
    if config is None:
        _console.print("[yellow]aborted; no file written.[/yellow]")
        raise typer.Exit(1)

    target.write_text(render_aegis_py(config))
    _console.print(f"\n[green]Wrote {target}[/green]")
    _console.print(
        f"[dim]Run [bold]aegis[/bold] to start the interactive TUI, "
        f"or [bold]aegis serve[/bold] for headless mode.[/dim]")


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit."),
    agent: str = typer.Option(None, "--agent", "-a",
                              help="Named agent profile to use."),
    cwd: str = typer.Option(".", "--cwd",
                            help="Working dir for the harness subprocess."),
    clean: bool = typer.Option(
        False, "--clean",
        help="Ignore prior workspace state; start fresh"),
) -> None:
    """Run the interactive aegis session (default when no subcommand)."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    name = agent or default_agent
    if name not in agents:
        _console.print(
            f"[red]Unknown agent {name!r}. Known: {sorted(agents)}[/red]")
        raise typer.Exit(1)

    root = find_project_root() or Path.cwd()
    effective_cwd = str(root) if cwd == "." else cwd

    try:
        queues = load_queues(root / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, effective_cwd, mcp_url, handle)

    AegisApp(agents, name, make_session, AegisMCP(), queues=queues,
             clean=clean).run()


async def _serve(*, agents, default_agent, make_session, mcp, tg,
                 stop: asyncio.Event, queues: dict | None = None) -> None:
    from aegis.queue import InboxRouter, QueueManager

    inbox = InboxRouter()
    mgr = SessionManager(agents, default_agent, make_session, mcp,
                         inbox=inbox)
    qm = QueueManager(queues or {}, mgr, inbox)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    tasks = []
    if tg is not None:
        from aegis.telegram.bot import BotClient
        from aegis.telegram.frontend import TelegramFrontend
        bot = BotClient(tg.token)
        fe = TelegramFrontend(bot, mgr, chat_id=tg.chat_id,
                              auto_prompt=tg.auto_prompt)
        tasks.append(asyncio.create_task(fe.run(bot)))
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()


@app.command()
def serve(cwd: str = typer.Option(".", "--cwd")) -> None:
    """Run the headless daemon (MCP plane + optional Telegram)."""
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    root = find_project_root() or Path.cwd()
    effective = str(root) if cwd == "." else cwd

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, effective, mcp_url, handle)

    try:
        queues = load_queues(root / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    tcfg = load_telegram_config(root / ".aegis.py")
    tg = tcfg if tcfg.token and tcfg.chat_id else None
    if tg is None:
        _console.print("[yellow]No telegram_token/chat_id — "
                       "headless MCP-only.[/yellow]")

    async def main_async():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await _serve(agents=agents, default_agent=default_agent,
                     make_session=make_session, mcp=AegisMCP(),
                     tg=tg, stop=stop, queues=queues)

    asyncio.run(main_async())


# Workflow subcommand group --------------------------------------------
workflow_app = typer.Typer(help="Author + run aegis workflows.")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("list")
def workflow_list_cmd() -> None:
    """List all @workflow-decorated functions discovered via .aegis.py."""
    try:
        load_config()    # loads .aegis.py → @workflow decorators fire
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    from aegis.workflow import list_workflows
    names = list_workflows()
    if not names:
        _console.print("[yellow]no workflows registered.[/yellow]")
        return
    for n in names:
        typer.echo(n)


@workflow_app.command(
    "run",
    context_settings={"allow_extra_args": True,
                      "ignore_unknown_options": True})
def workflow_run_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="workflow name"),
) -> None:
    """Run a workflow by name. Pass kwargs as ``--key=value``.

    All kwargs arrive as strings; the workflow body coerces if needed.
    """
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    root = find_project_root() or Path.cwd()
    try:
        queues = load_queues(root / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Parse trailing --key=value kwargs from ctx.args.
    kwargs: dict[str, str] = {}
    for tok in ctx.args:
        if not tok.startswith("--") or "=" not in tok:
            _console.print(f"[red]bad kwarg: {tok!r} (use --key=value)[/red]")
            raise typer.Exit(1)
        k, v = tok[2:].split("=", 1)
        kwargs[k.replace("-", "_")] = v

    from aegis.workflow import get_workflow, list_workflows, run_workflow

    if get_workflow(name) is None:
        _console.print(
            f"[red]unknown workflow: {name!r}. "
            f"Available: {list_workflows()}[/red]")
        raise typer.Exit(1)

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(root), mcp_url, handle)

    async def main_async():
        from aegis.queue import InboxRouter, QueueManager
        inbox = InboxRouter(state_dir=root / ".aegis" / "state")
        mgr = SessionManager(agents, default_agent, make_session,
                             AegisMCP(), inbox=inbox)
        qm = QueueManager(queues, mgr, inbox,
                          state_dir=root / ".aegis" / "state")
        mgr.attach_queue_manager(qm)
        mgr._mcp.bind(mgr)
        await mgr._mcp.start()
        await qm.start()
        try:
            out = await run_workflow(
                name, kwargs, bridge=mgr, queue_manager=qm,
                inbox_router=inbox,
                state_dir=root / ".aegis" / "state")
        finally:
            await qm.stop()
            await mgr.close_all()
            await mgr._mcp.stop()
        return out

    out = asyncio.run(main_async())
    typer.echo(out["status"])
    if out["status"] == "ok":
        typer.echo(out.get("result", ""))
    else:
        typer.echo(out.get("error", ""))
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
