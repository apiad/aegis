"""The AFK coordinator: a board of prompt cards, emptied while you sleep.

See docs/superpowers/specs/2026-09-25-afk-coordinator-design.md. The
workflows are registered here; everything they call lives in the sibling
modules, which hold no aegis state and are testable on their own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aegis.workflow import workflow
from aegis.workflows.builtins.afk.tick import run_tick

DEFAULTS = {
    "owner_type": "org",
    "worker_queue": "afk",
    "max_in_flight": 5,
    "weekly_stop_at": 60,
    "session_stop_at": 70,
    "max_attempts": 2,
    "review_changed_files": 5,
    "vague_body_chars": 400,
    "acceptance_markers": ("done when", "acceptance"),
    "gate_commands": ("make check", "make test"),
    "priority_order": ("Urgent", "Important", "Normal"),
    "allow_auto_done": False,
    "stall_after_s": 1800,
}
# `notify_cmd` is deliberately absent. The spec puts notification in slice 7,
# and a config key that is read, documented and does nothing is worse than an
# absent one: the first person to set it concludes the loop is broken.


@workflow("afk")
async def afk(engine, **kwargs) -> str:
    """One reconciler tick over the board: reap, gate on quota, start."""
    cfg = {**DEFAULTS, **engine.config, **kwargs}
    for required in ("owner", "project", "repo_root"):
        if not cfg.get(required):
            raise ValueError(f"afk workflow needs {required!r} in its args")
    # Schedule args arrive as strings; DEFAULTS also holds tuples, so
    # go via str() rather than asking int() to take the union.
    cfg["project"] = int(str(cfg["project"]))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return await run_tick(engine, cfg, now=now)
