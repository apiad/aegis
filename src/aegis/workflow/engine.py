from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from aegis.config import find_project_root
from aegis.mcp.bridge import SessionInfo
from aegis.queue.schema import now_iso
from aegis.workflow.decorator import WorkflowError


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

    # ── bash ─────────────────────────────────────────────────────────
    async def bash(self, cmd: str, *,
                   cwd: str | Path | None = None,
                   timeout: float | None = None,
                   env: dict | None = None,
                   ) -> subprocess.CompletedProcess:
        """Async shell. cwd defaults to project root (find_project_root)
        or os.getcwd(); timeout=None means wait forever. On timeout,
        raises WorkflowError after killing the subprocess."""
        if cwd is None:
            cwd = str(find_project_root() or os.getcwd())
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            raise WorkflowError(
                f"bash timed out after {timeout}s: {cmd}")
        return subprocess.CompletedProcess(
            args=cmd, returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"))
