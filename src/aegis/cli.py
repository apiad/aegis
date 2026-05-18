from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import ConfigError, load_config, write_init_scaffold
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
    try:
        write_init_scaffold(Path.cwd() / ".aegis.py")
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

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, cwd, mcp_url, handle)

    AegisApp(agents, name, make_session, AegisMCP()).run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
