"""Typer subcommand: ``aegis queue ls / show`` — a READ-ONLY view of the
queue logs under ``.aegis/state/queues/*.jsonl``.

**Why there is no ``aegis queue resume``, and why one must not be added
here.** The daemon's unix socket (``daemon/server.py``) is a
view-attachment stream, not request/response RPC: a client attaches and
receives frames, and there is no verb a short-lived process can send to
ask the live brain to do something and wait for the answer. Resuming a
parked worker means rebuilding a harness under a session that only the
brain holds, so a CLI process cannot do it — and a subcommand that edited
the JSONL log instead would be lying about a worker it never touched.

The two surfaces that CAN act are the two already bound to a brain: the
MCP tools (``aegis_task_resume`` / ``aegis_task_retry``) and the TUI slash
commands (``/queues tasks``, ``/resume``). Giving the CLI an acting verb needs a
request/response daemon protocol first; that is a change to
``daemon/server.py``, not a command added beside these two.

Reading is a different matter: the JSONL log is the same file the manager
replays at boot, so folding it here gives exactly what the manager would
rebuild, from any shell, with no daemon running at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table

from aegis.queue.jsonl import read_records
from aegis.queue.replay import EVENT_STATUS
from aegis.state.workspace import state_dir as _state_dir

app = typer.Typer(help="Inspect queue tasks (read-only).")
console = Console()


def _queue_dir() -> Path:
    return _state_dir(Path.cwd()) / "queues"


def _fold(path: Path) -> dict[str, dict]:
    """One queue's log -> ``task_id -> merged record``.

    The same latest-aggregate fold ``queue.replay`` does, and for the same
    reason: last lifecycle event wins for the status, every record merges
    its fields, so the final dict carries the enqueued metadata plus
    whatever dispatch, stall and park added. Diagnostic records
    (``deferred``, ``worker_session``) merge their fields without moving
    the status, exactly as they do on a real replay.
    """
    tasks: dict[str, dict] = {}
    for rec in read_records(path):
        tid = rec.get("task_id")
        if tid is None:
            continue
        tasks.setdefault(tid, {}).update(rec)
        if "attempt" in rec or "attempts" in rec:
            tasks[tid]["attempts"] = rec.get("attempts", rec.get("attempt"))
        if rec.get("event") in EVENT_STATUS:
            tasks[tid]["status"] = EVENT_STATUS[rec["event"]]
    return tasks


def _all_tasks(queue: str | None) -> list[tuple[str, str, dict]]:
    """``(queue, task_id, merged record)`` across every log, newest first.

    Task ids are ULIDs, so sorting them IS sorting by enqueue time.
    """
    qdir = _queue_dir()
    if not qdir.exists():
        return []
    out: list[tuple[str, str, dict]] = []
    for path in sorted(qdir.glob("*.jsonl")):
        if queue is not None and path.stem != queue:
            continue
        for tid, rec in _fold(path).items():
            out.append((path.stem, tid, rec))
    out.sort(key=lambda row: row[1], reverse=True)
    return out


def _summary(payload: str, limit: int = 48) -> str:
    first = next((ln for ln in (payload or "").splitlines() if ln.strip()), "")
    return first if len(first) <= limit else first[: limit - 1] + "…"


@app.command("ls")
def list_tasks(
    queue: str = typer.Argument(None, help="only this queue"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="include finished tasks"),
) -> None:
    """Tasks and their states, newest first.

    Unfinished tasks only by default — pending, dispatched and the parked
    (`recoverable`) ones, which are the rows anyone has to decide
    something about. `--all` adds the history.
    """
    rows = _all_tasks(queue)
    if not rows:
        console.print("[yellow]no queue logs under .aegis/state/queues.[/yellow]")
        return
    live = {"pending", "dispatched", "recoverable"}
    if not all_tasks:
        rows = [r for r in rows if r[2].get("status") in live]
    if not rows:
        console.print("[yellow]no unfinished tasks. --all shows history.[/yellow]")
        return
    table = Table()
    # The task id is the one column that must survive a narrow terminal
    # intact: it is what `/resume` and `aegis_task_resume` take, and a
    # `01M3D7EN…` nobody can copy makes the whole table decorative. It
    # folds onto a second line rather than being ellipsized; the payload
    # summary is the column that gives way instead.
    table.add_column("task", no_wrap=False, overflow="fold")
    for col in ("queue", "status", "worker", "attempts"):
        table.add_column(col)
    table.add_column("payload", overflow="ellipsis")
    for qname, tid, rec in rows:
        table.add_row(
            tid,
            qname,
            str(rec.get("status", "?")),
            str(rec.get("worker_handle") or "—"),
            str(rec.get("attempts") or 0),
            _summary(rec.get("payload", "")),
        )
    console.print(table)
    if any(r[2].get("status") == "recoverable" for r in rows):
        console.print(
            "\n[dim]parked workers hold their conversation. Resume one from "
            "the TUI with /resume <task_id>, or from an agent with "
            "aegis_task_resume — not from here (see the module "
            "docstring).[/dim]"
        )


@app.command("show")
def show_task(task_id: str) -> None:
    """One task's folded state plus every record its log holds for it."""
    for qname, tid, rec in _all_tasks(None):
        if tid != task_id:
            continue
        console.print(Pretty(rec))
        path = _queue_dir() / f"{qname}.jsonl"
        console.print("\n[bold]Events:[/bold]")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("task_id") == task_id:
                console.print(Pretty(raw))
        return
    typer.echo(f"unknown task: {task_id}", err=True)
    raise typer.Exit(1)
