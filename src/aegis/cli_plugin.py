"""`aegis plugin ...` Typer subapp."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aegis.plugins import lockfile
from aegis.plugins.install import InstallError, install_plugin
from aegis.plugins.uninstall import UninstallError, uninstall_plugin

app = typer.Typer(name="plugin", help="Manage aegis plugins.")
console = Console()


@app.command("install")
def cmd_install(
    name: str,
    from_: Path | None = typer.Option(
        None, "--from",
        help="Install from a local path instead of the registry.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't prompt; accept defaults."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing installation."),
) -> None:
    """Install a plugin."""
    from aegis.plugins.install import resolve_and_install
    try:
        if from_ is not None:
            install_plugin(
                name=name, source=from_, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
        else:
            resolve_and_install(
                name=name, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
    except InstallError as exc:
        console.print(f"[red]install failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]installed[/] {name}")


@app.command("uninstall")
def cmd_uninstall(
    name: str,
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Uninstall a plugin."""
    try:
        uninstall_plugin(name=name, project_root=Path.cwd(), yes=yes, console=console)
    except UninstallError as exc:
        console.print(f"[red]uninstall failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]uninstalled[/] {name}")


@app.command("list")
def cmd_list() -> None:
    """List installed plugins."""
    data = lockfile.read_lock(Path.cwd())
    plugs = data.get("plugins") or []
    if not plugs:
        console.print("[dim]no plugins installed[/]")
        return
    table = Table(title="Installed plugins")
    table.add_column("Name"); table.add_column("Version"); table.add_column("Installed")
    for p in plugs:
        table.add_row(p.get("name", ""), p.get("version", ""), p.get("installed", ""))
    console.print(table)


@app.command("update")
def cmd_update(
    name: str | None = typer.Argument(None),
    yes: bool = typer.Option(False, "--yes", "-y"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Update an installed plugin (re-fetch + replace)."""
    from aegis.plugins.install import update_plugin

    targets: list[str]
    if name is not None:
        targets = [name]
    else:
        targets = [p["name"] for p in (lockfile.read_lock(Path.cwd()).get("plugins") or [])]
    if not targets:
        console.print("[dim]no plugins installed[/]"); return

    for t in targets:
        try:
            update_plugin(
                name=t, project_root=Path.cwd(),
                yes=yes, force=force, console=console,
            )
        except InstallError as exc:
            console.print(f"[red]{t} failed:[/] {exc}")
            raise typer.Exit(1)
        console.print(f"[green]updated[/] {t}")


@app.command("search")
def cmd_search(query: str) -> None:
    """Search registries for plugins matching `query`."""
    from aegis.plugins.install import search_plugins
    hits = search_plugins(query=query, project_root=Path.cwd())
    if not hits:
        console.print(f"[dim]no plugins match {query!r}[/]"); return
    for h in hits:
        console.print(
            f"[bold]{h['name']}[/] {h['version']}  "
            f"[dim]from {h['registry']}[/]"
        )
        if h.get("description"):
            console.print(f"  {h['description']}")


@app.command("show")
def cmd_show(name: str) -> None:
    """Show details of an installed plugin."""
    data = lockfile.read_lock(Path.cwd())
    for p in data.get("plugins", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k == "file_hashes":
                    console.print(f"file_hashes: <{len(v)} files>")
                else:
                    console.print(f"{k}: {v}")
            return
    console.print(f"[red]not installed:[/] {name}")
    raise typer.Exit(1)
