"""Mirroring each running worker's task list onto its card.

Costs no agent calls: a read off the session manager and a write to the
board. That is why it runs at a much tighter cadence than the reconciler.

This module never writes Status, never starts anything and never reaps
anything. The two schedules are safe to run alongside each other precisely
because they write disjoint fields.
"""

from __future__ import annotations

import json
from datetime import datetime

from aegis.workflows.builtins.afk.board import (
    run_gh,
    BoardError,
    fetch_board,
    parse_marker,
    set_field,
    upsert_comment,
)
from aegis.workflows.builtins.afk.render import PLAN_PLACEHOLDER, replace_section

FIELDS = {"status": "Status", "progress": "Progress"}
STATUS_RUNNING = "Running"


def _fmt_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"


def _epoch(value) -> float | None:
    """``PlanSnapshot.updated_at`` as epoch seconds, or None if unreadable.

    The tracker writes it as an ISO 8601 string (``datetime.now(UTC)
    .isoformat()``), so subtracting it from a ``time.time()`` float raises
    TypeError on every real reading. Numbers are accepted too, because a
    caller holding an epoch already has nothing to convert.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        # A hand-written or future format. Unknown age reads as fresh:
        # calling a plan stalled because we cannot parse its clock would
        # put a false alarm on the board.
        return None


def format_rollup(snapshot, *, now: float, stall_after_s: float) -> str:
    """One glanceable line for the Progress field.

    An absent plan is a reading, not a blank: a worker ignoring the
    task-list instruction is something the operator wants to see.
    """
    if snapshot is None or not snapshot.total:
        return "no plan reported"
    stamp = _epoch(snapshot.updated_at)
    age = now - (stamp if stamp is not None else now)
    parts = [f"{snapshot.done}/{snapshot.total}"]
    if snapshot.current:
        parts.append(snapshot.current)
    if snapshot.current_working_s is not None:
        parts.append(_fmt_seconds(snapshot.current_working_s))
    line = " · ".join(parts)
    if age >= stall_after_s:
        return f"stalled {_fmt_seconds(age)} on {line}"
    return line


def format_plan(state) -> str:
    """The full checklist for the pinned comment's Plan section."""
    if state is None or not getattr(state, "tasks", ()):
        return PLAN_PLACEHOLDER
    lines = []
    for task in state.tasks:
        box = "x" if task.status == "completed" else " "
        suffix = ""
        if task.working_s is not None:
            suffix = f" — {_fmt_seconds(task.working_s)}"
        arrow = "  ← running" if task.status == "in_progress" else ""
        lines.append(f"- [{box}] {task.label}{suffix}{arrow}")
    return "\n".join(lines)


async def read_marker(engine, card) -> dict[str, str]:
    """The coordinator's marker plus the comment body it came from.

    The body travels with it because the Plan section is edited in place:
    rewriting the comment from scratch here would erase the Coordinator and
    Result sections, which this schedule has no way to reconstruct.
    """
    raw = await run_gh(
        engine,
        ["gh", "api", f"repos/{card.repo}/issues/{card.number}/comments", "--paginate"],
    )
    for c in json.loads(raw or "[]"):
        marker = parse_marker(c.get("body") or "")
        if marker:
            return {**marker, "_body": c.get("body") or ""}
    return {}


def snapshot_for(engine, handle: str | None):
    """The worker's plan roll-up, or None.

    Read off ``SessionInfo.plan`` rather than built from
    ``engine.plan_state``: only the roll-up carries ``updated_at``, and
    ``updated_at`` is the entire basis of stall detection. A snapshot
    assembled here with ``updated_at=now`` is never stale by construction,
    so the stall check would be a branch that can never be taken.
    """
    if not handle:
        return None
    for info in engine.list_sessions():
        if info.handle == handle:
            return info.plan
    return None


async def run_progress(engine, cfg: dict, *, now: float) -> str:
    schema, cards = await fetch_board(
        lambda argv: run_gh(engine, argv),
        owner=cfg["owner"],
        owner_type=cfg["owner_type"],
        project=cfg["project"],
    )
    status_field = (cfg.get("field_names") or {}).get("status", FIELDS["status"])
    progress_field = (cfg.get("field_names") or {}).get("progress", FIELDS["progress"])
    running_name = (cfg.get("status_names") or {}).get("running", STATUS_RUNNING)

    written = 0
    for card in cards:
        if card.fields.get(status_field) != running_name:
            continue
        try:
            marker = await read_marker(engine, card)
            task_id = marker.get("task")
            state = engine.task_status(task_id) if task_id else None
            handle = (state or {}).get("worker_handle")
            line = format_rollup(
                snapshot_for(engine, handle),
                now=now,
                stall_after_s=float(cfg["stall_after_s"]),
            )
            if line == card.fields.get(progress_field):
                continue

            await set_field(
                lambda argv: run_gh(engine, argv),
                schema,
                card,
                field=progress_field,
                value=line,
            )
            body = marker.get("_body") or ""
            if body:
                plan = engine.plan_state(handle) if handle else None
                await upsert_comment(
                    lambda argv: run_gh(engine, argv),
                    card,
                    body=replace_section(body, "Plan", format_plan(plan)),
                )
            written += 1
        except BoardError as e:
            engine.log(f"afk-progress: #{card.number}: {e}")
        except Exception as e:  # noqa: BLE001
            engine.log(f"afk-progress: #{card.number} raised {e!r}")
    return f"updated {written} card(s)"
