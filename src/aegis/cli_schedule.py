"""Typer subcommand: ``aegis schedule list / show / run / enable /
disable / logs / push / remove``.

Reads schedules from ``.aegis.yaml`` (+ overlay files) via the YAML
loader; the snapshot + JSONL files written by the in-flight scheduler
provide the runtime view. Edits go through
:mod:`aegis.config.edit` so operator comments survive.

``aegis schedule run`` invokes the workflow directly without going
through a running serve — short-circuits MCP/queue infrastructure for
fast smoke tests. To trigger a real fire on the daemon, prefer the
TUI ``F`` action or the (future) MCP control tool.

The ``--remote <peer>`` flag on ``list``/``show``/``logs`` routes the
verb through the remote-plane HTTP client; ``push --to <peer>`` and
``remove --remote <peer>`` reach across to a peer's schedule store.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table
from ruamel.yaml import YAML

from aegis.config.edit import set_schedule_enabled
from aegis.config.yaml_loader import load_config
from aegis.remote.client import (
    remote_schedule_list,
    remote_schedule_logs,
    remote_schedule_push,
    remote_schedule_remove,
    remote_schedule_show,
)
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


def _read_local_schedule(cfg, name: str) -> dict:
    if name not in cfg.schedules:
        raise KeyError(name)
    return dict(cfg.schedules[name])


def _self_name(cfg) -> str:
    rp = getattr(cfg, "remote_plane", None)
    if rp is not None:
        return getattr(rp, "peer_name", None) or "unknown"
    return "unknown"


def _print_schedule_table(schedules: list[dict]) -> None:
    """Render a list of schedule dicts (HTTP list payload shape) as a table."""
    table = Table()
    for col in ("name", "trigger", "next", "fires", "status"):
        table.add_column(col)
    for entry in schedules:
        trigger = entry.get("cron") or entry.get("fire_at") or "?"
        nxt = entry.get("next_fire") or "—"
        fires = entry.get("fire_count", 0)
        status = "paused" if not entry.get("enabled", True) else "armed"
        table.add_row(
            entry.get("name", "?"), str(trigger), str(nxt),
            str(fires), status)
    console.print(table)


def _load_spec_file(path: Path) -> dict:
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        return YAML(typ="safe").load(text) or {}
    return json.loads(text)


def _resolve_remote(cfg, name: str):
    if name not in cfg.remotes:
        typer.echo(f"unknown remote {name!r}", err=True)
        raise typer.Exit(1)
    return cfg.remotes[name]


@app.command("list")
def list_schedules(
    remote: str = typer.Option(None, "--remote", help="peer name"),
) -> None:
    """Tabular view of all schedules (local or remote)."""
    root, cfg = _cfg()
    if remote is not None:
        spec = _resolve_remote(cfg, remote)
        result = asyncio.run(remote_schedule_list(spec))
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)
        _print_schedule_table(result.get("schedules", []))
        return
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
def show_schedule(
    name: str,
    remote: str = typer.Option(None, "--remote", help="peer name"),
) -> None:
    """Full config + last 10 fires (local or remote)."""
    root, cfg = _cfg()
    if remote is not None:
        spec = _resolve_remote(cfg, remote)
        result = asyncio.run(remote_schedule_show(spec, name))
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)
        console.print(Pretty(result))
        return
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
    remote: str = typer.Option(None, "--remote", help="peer name"),
) -> None:
    root, cfg = _cfg()
    if remote is not None:
        spec = _resolve_remote(cfg, remote)
        result = asyncio.run(remote_schedule_logs(spec, name, tail=tail))
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)
        for record in result.get("records", []):
            console.print(Pretty(record))
        return
    log = _log_path(root, name)
    if not log.exists():
        typer.echo(f"no log for {name}", err=True)
        raise typer.Exit(1)
    for line in log.read_text().splitlines()[-tail:]:
        console.print(Pretty(json.loads(line)))


@app.command("push")
def push_schedule(
    to: str = typer.Option(..., "--to", help="remote peer name"),
    name: str = typer.Option(None, "--name", help="local schedule name"),
    file: Path = typer.Option(None, "--file", help="YAML/JSON spec file"),
) -> None:
    """Push a schedule to a remote peer."""
    _, cfg = _cfg()
    if file is not None:
        spec_body = _load_spec_file(file)
        name = name or file.stem
    elif name is not None:
        try:
            spec_body = _read_local_schedule(cfg, name)
        except KeyError:
            typer.echo(f"unknown schedule: {name}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo("--name or --file is required", err=True)
        raise typer.Exit(1)
    remote_spec = _resolve_remote(cfg, to)
    result = asyncio.run(remote_schedule_push(
        remote_spec, name=name, spec_body=spec_body,
        pushed_from=f"peer:{_self_name(cfg)}"))
    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)
    typer.echo(f"pushed {result['name']} → {to} ({result['written_to']})")


@app.command("remove")
def remove_schedule(
    name: str,
    remote: str = typer.Option(..., "--remote", help="peer name"),
) -> None:
    """Remove a pushed schedule from a remote peer."""
    _, cfg = _cfg()
    spec = _resolve_remote(cfg, remote)
    result = asyncio.run(remote_schedule_remove(spec, name))
    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)
    typer.echo(f"removed {name} from {remote}")
