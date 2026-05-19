from __future__ import annotations

import asyncio
import signal
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import (
    ConfigError, find_project_root, load_config, load_telegram_config,
    write_init_scaffold,
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
            v = _pkg_version("aegis")
        except PackageNotFoundError:        # not installed (rare in dev)
            v = "0.0.0+unknown"
        typer.echo(f"aegis {v}")
        raise typer.Exit()


@app.command()
def init() -> None:
    """Create a .aegis.py config scaffold in the current directory."""
    root = find_project_root()
    try:
        write_init_scaffold((root or Path.cwd()) / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print("[green]Created .aegis.py[/green]")


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

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, effective_cwd, mcp_url, handle)

    AegisApp(agents, name, make_session, AegisMCP()).run()


async def _serve(*, agents, default_agent, make_session, mcp, tg,
                 stop: asyncio.Event) -> None:
    mgr = SessionManager(agents, default_agent, make_session, mcp)
    mcp.bind(mgr)
    await mcp.start()
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
                     tg=tg, stop=stop)

    asyncio.run(main_async())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
