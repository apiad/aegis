"""`aegis budget` CLI subapp."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import typer
from rich.console import Console
from rich.table import Table

from aegis.remote.client import remote_budget_list, remote_budget_show

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


def _cfg():
    from aegis.config import find_project_root, load_queues
    from aegis.config.yaml_loader import load_config as _load_yaml
    root = find_project_root() or Path.cwd()
    queues = load_queues(root)
    # Remotes come from the YAML config; fall back gracefully if absent.
    try:
        yaml_cfg = _load_yaml(root)
        remotes = yaml_cfg.remotes
    except Exception:  # noqa: BLE001
        remotes = {}
    return SimpleNamespace(queues=queues, remotes=remotes)


def _load_jsonl(state_dir: Path, queue: str) -> list[dict]:
    log = state_dir / "queues" / f"{queue}.jsonl"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines()
            if l.strip()]


@app.command("list")
def list_budgets(
    remote: str = typer.Option(None, "--remote"),
) -> None:
    cfg = _cfg()
    if remote is not None:
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_list(cfg.remotes[remote]))
    else:
        from aegis.budget.evaluator import evaluate_budgets
        state_dir = Path.cwd() / ".aegis" / "state"
        now = datetime.now(timezone.utc)
        rows = []
        for name, q in cfg.queues.items():
            if not q.budgets:
                rows.append({"name": name, "budgets_count": 0,
                              "status": "no-budget"})
                continue
            # Filter tail to terminal events within longest window.
            cutoff = now - max(b.window for b in q.budgets)
            tail = []
            for rec in _load_jsonl(state_dir, name):
                if rec.get("event") not in ("completed", "failed"):
                    continue
                ts_str = rec.get("completed_at", "")
                try:
                    ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if ts >= cutoff:
                    tail.append(rec)
            d = evaluate_budgets(tail, q.budgets, now)
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok" if d.allowed else "blocked"})
        result = {"queues": rows}

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    table = Table()
    table.add_column("QUEUE")
    table.add_column("BUDGETS")
    table.add_column("STATUS")
    for row in result["queues"]:
        table.add_row(row["name"],
                      str(row.get("budgets_count", "?")),
                      row.get("status", "?"))
    _console.print(table)


@app.command("show")
def show_budget(
    queue: str,
    remote: str = typer.Option(None, "--remote"),
) -> None:
    cfg = _cfg()
    if remote is not None:
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_show(cfg.remotes[remote], queue))
    else:
        from aegis.budget.evaluator import evaluate_budgets
        if queue not in cfg.queues:
            typer.echo(f"unknown queue {queue!r}", err=True)
            raise typer.Exit(1)
        q = cfg.queues[queue]
        if not q.budgets:
            typer.echo(f"queue {queue!r} has no budgets configured.")
            return
        state_dir = Path.cwd() / ".aegis" / "state"
        now = datetime.now(timezone.utc)
        tail = _load_jsonl(state_dir, queue)
        d = evaluate_budgets(tail, q.budgets, now)
        result = {
            "name": queue, "allowed": d.allowed,
            "checks": [{"constraint": c.constraint, "limit": str(c.limit),
                          "spent": str(c.spent), "window": c.window_str,
                          "allowed": c.allowed, "headroom": str(c.headroom)}
                         for c in d.checks],
        }

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    table = Table(title=f"budget for queue {queue!r}")
    for col in ("CONSTRAINT", "LIMIT", "SPENT", "WINDOW",
                "HEADROOM", "STATUS"):
        table.add_column(col)
    for c in result["checks"]:
        status = "✓" if c["allowed"] else "⛔"
        table.add_row(c["constraint"], c["limit"], c["spent"],
                       c["window"], c["headroom"], status)
    _console.print(table)
