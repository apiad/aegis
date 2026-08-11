"""``aegis comms`` — read back who talked to whom.

A write-only ledger cannot be verified: there is no way to know it works
except opening the file by hand. This is how the artifact gets exercised the
way it will actually be used.
"""
from __future__ import annotations

from pathlib import Path

import typer

from aegis.comms.persistence import CommsLedger

comms_app = typer.Typer(add_completion=False,
                        help="Inspect the inter-agent call ledger.")


def filter_rows(rows: list[dict], handle: str | None = None,
                thread: str | None = None, family: str | None = None,
                since_iso: str | None = None) -> list[dict]:
    """Pure filter over ledger records. ``handle`` matches either end of a
    call — the point of the ledger is the conversation, not one side of it."""
    out = []
    for row in rows:
        if handle:
            to = row.get("to") or {}
            if row.get("from") != handle and to.get("id") != handle:
                continue
        if thread and row.get("thread") != thread:
            continue
        if family and row.get("family") != family:
            continue
        if since_iso and str(row.get("ts", "")) < since_iso:
            continue
        out.append(row)
    return out


def _format(row: dict) -> str:
    to = row.get("to") or {}
    counterpart = f"{to.get('kind')}:{to.get('id')}" if to else "-"
    flag = "" if row.get("outcome") == "ok" else "  ERROR"
    return (f"{row.get('ts', ''):22} "
            f"{row.get('from') or '(unattributed)':22} "
            f"{row.get('verb', ''):26} {counterpart:28} "
            f"{row.get('thread', ''):14}{flag}")


@comms_app.command("list")
def list_calls(
    handle: str = typer.Option(
        None, "--handle", help="Only calls with this agent at either end."),
    thread: str = typer.Option(
        None, "--thread", help="Only calls on this thread id."),
    family: str = typer.Option(
        None, "--family",
        help="conversation | coordination | introspection | admin"),
    since: str = typer.Option(
        None, "--since", help="ISO timestamp; drop anything older."),
) -> None:
    ledger = CommsLedger(Path.cwd() / ".aegis" / "state")
    rows = filter_rows(ledger.read_all(), handle=handle, thread=thread,
                       family=family, since_iso=since)
    if not rows:
        typer.echo("no aegis calls recorded")
        return
    for row in rows:
        typer.echo(_format(row))
