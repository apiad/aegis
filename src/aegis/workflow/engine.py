from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from aegis.config import find_project_root
from aegis.mcp.bridge import SessionInfo
from aegis.queue.schema import InboxMessage, new_ulid as _new_ulid, now_iso
from aegis.workflow.decorator import (
    PredicateFailed, SubagentSpawnError, WorkflowError,
)

__all__ = [
    "WorkflowEngine", "PredicateFailed", "SubagentSpawnError",
    "WorkflowError",
]


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
    (AppBridge, QueueManager, InboxRouter) and to a ``WorkflowRunner``
    on the bridge that mediates send-and-await-reply, ask_human, spawn,
    bash, ledger persistence, and narration. Tracks _spawned_handles
    for auto-close and _touched_handles for auto-drain at runner exit.
    """

    def __init__(self, *,
                 # New canonical kwargs:
                 name: str | None = None,
                 workflow_id: str | None = None,
                 host: str | None = None,
                 config: dict | None = None,
                 # Legacy aliases (kept for back-compat):
                 workflow_name: str | None = None,
                 workflow_run_id: str | None = None,
                 caller_handle: str | None = None,
                 # Plumbing:
                 bridge=None, queue_manager=None, inbox_router=None,
                 state_dir: Path | None = None,
                 now: Callable[[], str] = now_iso,
                 drain_timeout: float = 30.0) -> None:
        name_val = name if name is not None else workflow_name
        if name_val is None:
            raise TypeError(
                "WorkflowEngine: 'name' (or legacy 'workflow_name') is required")
        self.name = self.workflow_name = name_val

        wid_val = workflow_id if workflow_id is not None else workflow_run_id
        if wid_val is None:
            raise TypeError(
                "WorkflowEngine: 'workflow_id' (or legacy "
                "'workflow_run_id') is required")
        self.workflow_id = self.workflow_run_id = wid_val

        host_val = host if host is not None else caller_handle
        self.host = self.caller_handle = host_val

        self.config: dict = dict(config) if config else {}

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
        """Single-line narration. Writes to stderr + JSONL under state_dir.

        Kept sync for v1 back-compat with existing workflows; the
        catalog seeds may also call this without ``await``."""
        print(f"[workflow:{self.name}] {message}",
              file=sys.stderr, flush=True)
        if self._state_dir is None:
            return
        path = (Path(self._state_dir) / "workflows"
                / f"{self.workflow_id}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": self._now(), "message": message}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        runner = self._runner()
        if runner is not None and hasattr(runner, "narrate"):
            try:
                coro = runner.narrate(self.workflow_id, self.host, message)
                if asyncio.iscoroutine(coro):
                    asyncio.create_task(coro)
            except Exception:  # noqa: BLE001
                pass

    # ── bash ─────────────────────────────────────────────────────────
    async def bash(self, cmd: str, *,
                   cwd: str | Path | None = None,
                   timeout: float | None = None,
                   env: dict | None = None,
                   ) -> "_BashResult":
        """Async shell. Delegates to ``bridge.workflow_runner.run_bash``
        when the runner exposes one (test bridges intercept here);
        otherwise runs the command via ``asyncio.create_subprocess_shell``
        with ``cwd`` defaulting to the project root.

        Returns a ``_BashResult`` (``subprocess.CompletedProcess``
        subclass) so callers may use either attribute access
        (``.returncode``/``.stdout``/``.stderr``) or dict-style
        indexing (``["exit"]``/``["stdout"]``/``["stderr"]``) — the
        latter is what ``bash_predicate`` retry-with templates expect."""
        runner = self._runner()
        if runner is not None and hasattr(runner, "run_bash"):
            res = await runner.run_bash(cmd, cwd=cwd, timeout=timeout)
            if isinstance(res, dict):
                return _BashResult(
                    args=cmd, returncode=res.get("exit", 0),
                    stdout=res.get("stdout", ""),
                    stderr=res.get("stderr", ""))
            return res
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
        return _BashResult(
            args=cmd, returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"))

    async def bash_predicate(self, cmd: str, *,
                             retry_with,
                             max_retries: int = 3,
                             cwd: str | Path | None = None,
                             timeout: float | None = None,
                             ) -> "_BashResult":
        """Run ``cmd`` repeatedly until it exits 0 or ``max_retries`` is
        reached. On each non-zero exit, send ``retry_with`` feedback to
        ``self.host`` and try again.

        ``retry_with`` may be a string template
        (formatted with ``{exit}``/``{stdout}``/``{stderr}``) or a
        callable that takes the bash result dict and returns the
        feedback. Raises ``PredicateFailed`` if the budget is
        exhausted."""
        attempts = 0
        last_result: _BashResult | None = None
        while True:
            res = await self.bash(cmd, cwd=cwd, timeout=timeout)
            attempts += 1
            if res["exit"] == 0:
                return res
            last_result = res
            if attempts > max_retries:
                raise PredicateFailed(
                    cmd,
                    {"exit": res["exit"], "stdout": res["stdout"],
                     "stderr": res["stderr"]},
                    attempts=attempts)
            if callable(retry_with):
                feedback = retry_with({
                    "exit": res["exit"], "stdout": res["stdout"],
                    "stderr": res["stderr"]})
            else:
                feedback = retry_with.format(
                    exit=res["exit"], stdout=res["stdout"],
                    stderr=res["stderr"])
            if self.host is not None:
                await self.send(self.host, feedback)

    async def parallel(self, coros) -> list:
        """Run the given awaitables concurrently; return their results
        in order. Thin wrapper around ``asyncio.gather``."""
        return list(await asyncio.gather(*coros))

    # ── delegate (queue-worker pattern; legacy) ──────────────────────
    async def delegate(self, queue: str, payload: str) -> str:
        """Enqueue a one-shot task on the named queue; await the worker's
        callback; return its final assistant text. Raises WorkflowError
        on unknown queue, worker failure, or substrate error."""
        handle = f"workflow:{self.name}:{_new_ulid()}"
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
                    alias: str | None = None,
                    handle: str | None = None) -> str:
        """Spawn a fresh subagent of ``profile``; track for auto-close on
        workflow exit. ``alias`` is the preferred kwarg; ``handle`` is a
        legacy alias accepted for back-compat. Returns the new handle."""
        requested = alias if alias is not None else handle
        runner = self._runner()
        try:
            if runner is not None and hasattr(runner, "spawn_subagent"):
                h = await runner.spawn_subagent(profile, alias=requested)
            else:
                h = await self._bridge.spawn(profile, handle=requested)
        except Exception as e:  # noqa: BLE001
            raise SubagentSpawnError(
                f"spawn({profile!r}, alias={requested!r}) failed: {e}"
            ) from e
        self._spawned_handles.add(h)
        return h

    async def close(self, handle: str) -> None:
        """Close a spawned subagent. Raises ``ValueError`` if ``handle``
        is the host. Idempotent for unknown handles."""
        if self.host is not None and handle == self.host:
            raise ValueError(
                f"cannot close host handle: {handle!r}")
        if handle not in self._spawned_handles:
            return
        self._spawned_handles.discard(handle)
        runner = self._runner()
        try:
            if runner is not None and hasattr(runner, "close_session"):
                await runner.close_session(handle)
            else:
                await self._bridge.close(handle)
        except Exception:  # noqa: BLE001 — close is best-effort
            pass

    # ── send ─────────────────────────────────────────────────────────
    async def send(self, handle: str, prompt: str, *,
                   timeout: float | None = None) -> str:
        """Send ``prompt`` as a user-turn to ``handle`` (host or
        subagent); await the next complete assistant message from that
        handle; return its text."""
        self._touched_handles.add(handle)
        runner = self._runner()
        if runner is not None and hasattr(runner, "send_and_await_reply"):
            return await runner.send_and_await_reply(
                handle=handle, prompt=prompt,
                workflow_id=self.workflow_id,
                workflow_name=self.name,
                timeout=timeout)
        # Fallback: legacy fire-and-forget through inbox_router (returns "").
        # Used by code paths that haven't yet been migrated to the runner.
        if self._inbox is not None:
            msg = InboxMessage(
                sender=f"workflow:{self.name}",
                timestamp=self._now(),
                body=prompt)
            await self._inbox.deliver(handle, msg)
        return ""

    # ── checkpoint / resume ─────────────────────────────────────────
    async def checkpoint(self, name: str, payload: dict) -> None:
        """Persist a named checkpoint to the workflow's ledger so that a
        later ``resume_state()`` (in a fresh run with the same
        ``workflow_id``) returns ``payload``. Raises ``TypeError`` early
        if ``payload`` is not JSON-serializable."""
        json.dumps(payload)
        runner = self._runner()
        if runner is None or not hasattr(runner, "append_ledger"):
            raise RuntimeError(
                "checkpoint: bridge has no workflow_runner with append_ledger")
        record = {
            "kind": "checkpoint",
            "at": self._now(),
            "name": name,
            "payload": payload,
        }
        res = runner.append_ledger(self.workflow_id, record)
        if asyncio.iscoroutine(res):
            await res

    async def resume_state(self) -> dict | None:
        """Return the most recent checkpoint payload for this workflow
        run, or ``None`` if no checkpoint exists yet."""
        runner = self._runner()
        if runner is None or not hasattr(runner, "read_ledger"):
            return None
        records = runner.read_ledger(self.workflow_id)
        if asyncio.iscoroutine(records):
            records = await records
        for rec in reversed(records or []):
            if rec.get("kind") == "checkpoint":
                return rec.get("payload")
        return None

    # ── ask_human ────────────────────────────────────────────────────
    async def ask_human(self, question: str, *,
                        options: list[str] | None = None,
                        timeout: float | None = None) -> str:
        """Pose ``question`` to the human operator via the host's input
        bar (TUI) or Telegram (headless), await their reply, return it.

        Routes through ``bridge.workflow_runner.register_human_question``;
        the runner records the pending question and resolves the future
        when a matching reply arrives."""
        runner = self._runner()
        if runner is None or not hasattr(runner, "register_human_question"):
            raise RuntimeError(
                "ask_human: bridge has no workflow_runner")
        fut: asyncio.Future[str] = (
            asyncio.get_running_loop().create_future())
        await runner.register_human_question(
            host=self.host, workflow_id=self.workflow_id,
            question=question, options=options, fut=fut)
        if timeout is None:
            return await fut
        return await asyncio.wait_for(fut, timeout=timeout)

    # ── drain ────────────────────────────────────────────────────────
    async def drain(self, handle: str | None = None) -> None:
        """Await each touched handle's session to reach state == ready.
        Legacy primitive kept for back-compat with `tdd_step` and
        tests; the new ``send`` already blocks until reply, so most
        new workflows won't need this."""
        targets = (
            [handle] if handle is not None else list(self._touched_handles))
        for h in targets:
            await self._drain_one(h)

    async def _drain_one(self, handle: str) -> None:
        await asyncio.sleep(0)
        if self._inbox is None:
            return
        session = self._inbox._sessions.get(handle)
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

    # ── helpers ──────────────────────────────────────────────────────
    def _runner(self):
        return getattr(self._bridge, "workflow_runner", None)


class _BashResult(subprocess.CompletedProcess):
    """``CompletedProcess`` extended with dict-style access for catalog
    helpers (``result["exit"]``/``["stdout"]``/``["stderr"]``)."""

    def __getitem__(self, key):
        if key == "exit":
            return self.returncode
        if key == "stdout":
            return self.stdout
        if key == "stderr":
            return self.stderr
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
