"""``aegis models`` CLI subapp — inspect and manage the model registry.

Subcommands:
- ``refresh``  — synchronously refetch ``~/.cache/aegis/models.yaml`` from
  the upstream GitHub raw URL and force the in-memory registry to reload.
  Use this when you just pushed a new ``models.yaml`` and don't want to
  wait for the background TTL to expire.
- ``list``     — print the active registry (provider/model/context/prices).
- ``clear``    — delete the local cache so the next aegis boot falls back
  to the bundled YAML.
"""
from __future__ import annotations

from decimal import Decimal

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=True)
_console = Console()


@app.command("refresh")
def refresh_cmd() -> None:
    """Synchronously refetch the model registry from GitHub.

    Replaces ``~/.cache/aegis/models.yaml`` with the upstream copy and
    re-parses the in-memory registry. Exits nonzero if the fetch fails."""
    import aegis.models as models_mod
    from aegis.models.refresh import DEFAULT_URL, _fetch_and_write

    dest = models_mod.cache_path()
    _console.print(f"[dim]Fetching {DEFAULT_URL} → {dest}[/dim]")
    _fetch_and_write(DEFAULT_URL, dest)
    if not dest.exists():
        _console.print(
            "[red]refresh failed — cache file not written. "
            "Check connectivity / DNS to raw.githubusercontent.com.[/red]")
        raise typer.Exit(1)
    reg = models_mod.load_registry(force=True)
    n_models = sum(len(p.models) for p in reg.providers.values())
    _console.print(
        f"[green]✓ refreshed[/green]  "
        f"{len(reg.providers)} providers, {n_models} models  "
        f"(updated {reg.updated})")


@app.command("clear")
def clear_cmd() -> None:
    """Delete ``~/.cache/aegis/models.yaml`` so the next boot uses the
    bundled YAML and refires the background fetch."""
    import aegis.models as models_mod

    dest = models_mod.cache_path()
    if dest.exists():
        dest.unlink()
        _console.print(f"[green]✓ removed[/green] {dest}")
    else:
        _console.print(f"[dim](no cache to remove at {dest})[/dim]")


@app.command("list")
def list_cmd(provider: str = typer.Argument(
        None, help="Restrict to one provider (claude-code / gemini / opencode).")
) -> None:
    """Print the active registry — what aegis sees right now."""
    from aegis.models import load_registry

    reg = load_registry()
    targets = ([provider] if provider else list(reg.providers))
    for prov_name in targets:
        prov = reg.providers.get(prov_name)
        if prov is None:
            _console.print(f"[red]unknown provider: {prov_name}[/red]")
            continue
        t = Table(title=f"{prov_name}  (default_context={prov.default_context_window})",
                  title_justify="left", title_style="bold")
        t.add_column("model")
        t.add_column("label")
        t.add_column("context", justify="right")
        t.add_column("$in/MTok", justify="right")
        t.add_column("$out/MTok", justify="right")
        t.add_column("aliases")
        for name, entry in prov.models.items():
            in_p = (f"${entry.prices.input}" if entry.prices else "—")
            out_p = (f"${entry.prices.output}" if entry.prices else "—")
            ctx = f"{entry.context_window:,}" if entry.context_window else "—"
            t.add_row(name, entry.label or "—", ctx, in_p, out_p,
                      ", ".join(entry.aliases) or "—")
        _console.print(t)
        _console.print()
