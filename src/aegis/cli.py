from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import ConfigError, load_config, write_init_scaffold
from aegis.drivers import get_driver
from aegis.repl import run_repl

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


@app.command()
def init() -> None:
    """Create a .aegis.py config scaffold in the current directory."""
    try:
        write_init_scaffold(Path.cwd() / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print("[green]Created .aegis.py[/green]")


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
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
            f"[red]Unknown agent {name!r}. Known: {sorted(agents)}[/red]"
        )
        raise typer.Exit(1)
    profile = agents[name]
    driver = get_driver(profile.harness)
    session = driver.session(profile, cwd)
    asyncio.run(run_repl(session, _console))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
