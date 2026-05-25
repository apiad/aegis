"""Typer subcommand: ``aegis schedule list / show / run / enable /
disable / logs``.

Reads schedules from ``.aegis.yaml`` (+ overlay files) via the YAML
loader; the snapshot + JSONL files written by the in-flight scheduler
provide the runtime view. Edits go through
:mod:`aegis.config.edit` so operator comments survive.

``aegis schedule run`` invokes the workflow directly without going
through a running serve — short-circuits MCP/queue infrastructure for
fast smoke tests. To trigger a real fire on the daemon, prefer the
TUI ``F`` action or the (future) MCP control tool.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table

from aegis.config.edit import set_schedule_enabled
from aegis.config.yaml_loader import load_config
from aegis.state.workspace import state_dir as _state_dir

app = typer.Typer(help="Manage scheduled tasks.")
console = Console()


def _cfg():
    root = Path.cwd()
    cfg = load_config(root)
    return root, cfg


def _snap(root: Path) -> dict:
    snap_path = _state_dir(root) / "schedules.snapshot.json"
    if snap_path.exists():
        return json.loads(snap_path.read_text())
    return {}


def _log_path(root: Path, name: str) -> Path:
    return _state_dir(root) / "schedules" / f"{name}.jsonl"


@app.command("list")
def list_schedules() -> None:
    """Tabular view of all schedules."""
    root, cfg = _cfg()
    if not cfg.schedules:
        console.print("[yellow]no schedules declared.[/yellow]")
        return
    snap = _snap(root)
    table = Table()
    for col in ("name", "trigger", "next", "fires", "status"):
        table.add_column(col)
    for name, entry in cfg.schedules.items():
        trigger = entry.get("cron") or entry.get("fire_at", "?")
        slot = snap.get(name, {})
        nxt = slot.get("next_fire", "—")
        fires = slot.get("fire_count", 0)
        status = "paused" if not entry.get("enabled", True) else "armed"
        table.add_row(name, str(trigger), str(nxt), str(fires), status)
    console.print(table)


@app.command("show")
def show_schedule(name: str) -> None:
    """Full config + last 10 fires."""
    root, cfg = _cfg()
    if name not in cfg.schedules:
        typer.echo(f"unknown schedule: {name}", err=True)
        raise typer.Exit(1)
    entry = cfg.schedules[name]
    console.print(Pretty(entry))
    log = _log_path(root, name)
    if log.exists():
        lines = log.read_text().splitlines()[-10:]
        if lines:
            console.print("\n[bold]Last events:[/bold]")
            for line in lines:
                console.print(Pretty(json.loads(line)))


@app.command("run")
def run_schedule(name: str) -> None:
    """Invoke the workflow directly (no MCP / queue).

    Intended for smoke tests; the in-flight scheduler is bypassed.
    """
    root, cfg = _cfg()
    if name not in cfg.schedules:
        typer.echo(f"unknown schedule: {name}", err=True)
        raise typer.Exit(1)
    entry = cfg.schedules[name]
    from aegis.workflow.runner import run_workflow

    async def _run():
        return await run_workflow(
            entry["workflow"], dict(entry.get("args") or {}),
            bridge=None, queue_manager=None, inbox_router=None,
            state_dir=_state_dir(root))

    result = asyncio.run(_run())
    console.print(Pretty(result))
    if result.get("status") != "ok":
        raise typer.Exit(1)


@app.command("enable")
def enable_schedule(name: str) -> None:
    root, _ = _cfg()
    try:
        set_schedule_enabled(root, name, True)
    except (FileNotFoundError, KeyError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    console.print(f"[green]{name}: enabled[/green]")


@app.command("disable")
def disable_schedule(name: str) -> None:
    root, _ = _cfg()
    try:
        set_schedule_enabled(root, name, False)
    except (FileNotFoundError, KeyError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    console.print(f"[yellow]{name}: disabled[/yellow]")


@app.command("logs")
def schedule_logs(
    name: str,
    tail: int = typer.Option(20, "--tail", "-n",
                             help="lines from end"),
) -> None:
    root, _ = _cfg()
    log = _log_path(root, name)
    if not log.exists():
        typer.echo(f"no log for {name}", err=True)
        raise typer.Exit(1)
    for line in log.read_text().splitlines()[-tail:]:
        console.print(Pretty(json.loads(line)))
