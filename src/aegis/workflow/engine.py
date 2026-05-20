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
from aegis.queue.schema import InboxMessage, new_ulid as _new_ulid, now_iso
from aegis.workflow.decorator import WorkflowError


class _DelegationPromise:
    """Inbox-binding shape used by delegate(): receives one InboxMessage
    and resolves a Future. Lives only for the duration of one delegate
    call."""

    def __init__(self) -> None:
        self._future: asyncio.Future[InboxMessage] = (
            asyncio.get_event_loop().create_future())

    async def deliver(self, msg: InboxMessage) -> None:
        if not self._future.done():
            self._future.set_result(msg)

    def __await__(self):
        return self._future.__await__()


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

    # ── delegate ─────────────────────────────────────────────────────
    async def delegate(self, queue: str, payload: str) -> str:
        """Enqueue a one-shot task on the named queue; await the worker's
        callback; return its final assistant text. Raises WorkflowError
        on unknown queue, worker failure, or substrate error."""
        handle = f"workflow:{self.workflow_name}:{_new_ulid()}"
        promise = _DelegationPromise()
        self._inbox.bind_session(handle, promise)
        try:
            try:
                task_id, _pos = self._queue.enqueue(
                    queue, payload,
                    enqueued_by=handle, callback=True)
            except KeyError as e:
                raise WorkflowError(
                    f"unknown queue: {e.args[0]!r}") from e
            msg = await promise
            if msg.status == "error":
                raise WorkflowError(
                    f"task {task_id} failed: {msg.body}")
            return msg.body
        finally:
            self._inbox.unbind_session(handle)

    # ── spawn / close ────────────────────────────────────────────────
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        """Spawn a long-lived agent through the bridge. Tracks handle for
        auto-close on workflow exit. Returns the handle."""
        h = await self._bridge.spawn(profile, handle=handle)
        self._spawned_handles.add(h)
        return h

    async def close(self, handle: str) -> None:
        """Close a long-lived agent. Idempotent — silent no-op if the
        handle is unknown to this engine."""
        if handle not in self._spawned_handles:
            return
        self._spawned_handles.discard(handle)
        try:
            await self._bridge.close(handle)
        except Exception:  # noqa: BLE001 — close is best-effort
            pass

    # ── send ─────────────────────────────────────────────────────────
    def send(self, handle: str, message: str) -> None:
        """Enqueue a substrate-tagged message in handle's inbox.
        Sync, fire-and-forget. Returns immediately; the actual delivery
        is scheduled as an asyncio task (which inherits the calling
        context — workflows always run on the aegis event loop)."""
        msg = InboxMessage(
            sender=f"workflow:{self.workflow_name}",
            timestamp=self._now(),
            body=message)
        self._touched_handles.add(handle)
        asyncio.create_task(self._inbox.deliver(handle, msg))

    # ── drain ────────────────────────────────────────────────────────
    async def drain(self, handle: str | None = None) -> None:
        """Await each touched handle's session to reach state == ready.
        If handle is None, drain all touched handles. Per-handle ceiling
        of self._drain_timeout; on timeout, log a warning and continue
        (don't trap workflow shutdown on a hung agent)."""
        targets = (
            [handle] if handle is not None else list(self._touched_handles))
        for h in targets:
            await self._drain_one(h)

    async def _drain_one(self, handle: str) -> None:
        # Let any pending send()-scheduled deliver tasks fire so that a
        # touched handle has actually transitioned to working before we
        # decide whether the early-return path applies.
        await asyncio.sleep(0)
        session = (
            self._inbox._sessions.get(handle) if self._inbox else None)
        if session is None:
            return
        from aegis.tui.state import AgentState
        if session.state is AgentState.ready and not getattr(
                session, "_inbox_buffer", []):
            return
        deadline = asyncio.get_event_loop().time() + self._drain_timeout
        while True:
            if session.state is AgentState.ready and not getattr(
                    session, "_inbox_buffer", []):
                return
            if asyncio.get_event_loop().time() >= deadline:
                self.log(
                    f"drain timed out after {self._drain_timeout}s for "
                    f"handle={handle!r} (state={session.state.value})")
                return
            await asyncio.sleep(0.05)
