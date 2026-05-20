from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from aegis.mcp.bridge import SessionInfo
from aegis.queue.schema import now_iso


class WorkflowEngine:
    """Runtime handle a workflow receives as its first positional argument.

    Constructed once per workflow run; bound to live aegis substrate
    (AppBridge, QueueManager, InboxRouter). Tracks _spawned_handles for
    auto-close and _touched_handles for auto-drain at runner exit.
    """

    def __init__(self, *, workflow_name: str, workflow_run_id: str,
                 bridge, queue_manager, inbox_router,
                 caller_handle: str | None = None,
                 state_dir: Path | None = None,
                 now: Callable[[], str] = now_iso,
                 drain_timeout: float = 30.0) -> None:
        self.workflow_name = workflow_name
        self.workflow_run_id = workflow_run_id
        self.caller_handle = caller_handle
        self._bridge = bridge
        self._queue = queue_manager
        self._inbox = inbox_router
        self._state_dir = state_dir
        self._now = now
        self._drain_timeout = drain_timeout
        self._spawned_handles: set[str] = set()
        self._touched_handles: set[str] = set()

    # ── read-only passthroughs ───────────────────────────────────────
    def list_sessions(self) -> list[SessionInfo]:
        return self._bridge.list_sessions()

    def list_agents(self) -> list[str]:
        return self._bridge.list_agents()

    # ── log ──────────────────────────────────────────────────────────
    def log(self, message: str) -> None:
        print(f"[workflow:{self.workflow_name}] {message}",
              file=sys.stderr, flush=True)
        if self._state_dir is None:
            return
        path = (Path(self._state_dir) / "workflows"
                / f"{self.workflow_run_id}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": self._now(), "message": message}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
