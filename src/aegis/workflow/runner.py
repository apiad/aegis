from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis.queue.schema import new_ulid
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows,
)
from aegis.workflow.engine import WorkflowEngine


def _jsonable(value):
    """Coerce ``value`` to something json.dumps can handle; fall back to
    ``str()`` if it isn't natively serializable."""
    import json
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


async def run_workflow(
    name: str, kwargs: dict, *,
    bridge: Any, queue_manager: Any, inbox_router: Any,
    caller_handle: str | None = None,
    state_dir: Path | None = None,
    workflow_run_id: str | None = None,
) -> dict:
    """Build a WorkflowEngine, invoke the named workflow with kwargs,
    auto-drain touched handles + auto-close spawned handles in finally.
    Returns {status, result?, error?, workflow_run_id}.

    Legacy entry-point retained for the existing MCP ``aegis_run_workflow``
    callback path. New code prefers ``WorkflowRunner.start`` (non-blocking,
    cancellable, status-pollable).
    """
    run_id = workflow_run_id or new_ulid()
    fn = get_workflow(name)
    if fn is None:
        return {
            "status": "error",
            "error": (f"unknown workflow: {name!r}. "
                      f"Available: {list_workflows()}"),
            "workflow_run_id": run_id,
        }
    engine = WorkflowEngine(
        workflow_name=name, workflow_run_id=run_id,
        bridge=bridge, queue_manager=queue_manager,
        inbox_router=inbox_router,
        caller_handle=caller_handle, state_dir=state_dir)
    try:
        result = await fn(engine, **kwargs)
        return {"status": "ok", "result": result, "workflow_run_id": run_id}
    except WorkflowError as e:
        return {"status": "error", "error": str(e),
                "workflow_run_id": run_id}
    except Exception as e:  # noqa: BLE001 — unexpected crash → tagged
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": f"unexpected: {type(e).__name__}: {e}",
            "workflow_run_id": run_id,
        }
    finally:
        await _runner_cleanup(engine)


async def _runner_cleanup(engine: WorkflowEngine) -> None:
    """Best-effort teardown: drain all touched handles, then close every
    spawned handle. Each step swallows its own errors so a hung drain
    or a flaky close can't strand the runner."""
    try:
        await engine.drain()
    except Exception as e:  # noqa: BLE001 — drain is best-effort
        engine.log(f"runner cleanup: drain raised {type(e).__name__}: {e}")
    for handle in list(engine._spawned_handles):
        try:
            await engine.close(handle)
        except Exception as e:  # noqa: BLE001 — close is best-effort
            engine.log(
                f"runner cleanup: close({handle!r}) raised "
                f"{type(e).__name__}: {e}")


@dataclass
class _PendingQuestion:
    workflow_id: str
    host: str
    question: str
    options: list[str] | None
    fut: asyncio.Future


@dataclass
class _RunningWorkflow:
    id: str
    name: str
    host: str | None
    task: asyncio.Task
    engine: WorkflowEngine
    status: str = "running"
    result: Any = None
    error: str | None = None


class WorkflowRunner:
    """Asyncio-task-owned scheduler for workflow runs.

    Owns per-workflow state: running asyncio.Task, pending human
    questions, and runner-mediated bridges to host/subagent sessions.
    The MCP `aegis_run_workflow` / `aegis_workflow_status` /
    `aegis_workflow_cancel` tools delegate here.
    """

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge
        self._running: dict[str, _RunningWorkflow] = {}
        self._questions: dict[str, deque[_PendingQuestion]] = {}
        self._last_options: dict[str, list[str] | None] = {}
        self._state_dir: Path | None = None

    # ── ledger persistence ────────────────────────────────────────────
    def set_state_dir(self, path: Path) -> None:
        self._state_dir = Path(path)

    def _state_root(self) -> Path | None:
        if self._state_dir is not None:
            return self._state_dir
        # Fall back to the bridge's state_dir (FakeBridge in tests, the
        # TUI app exposes one at the queue manager level in prod).
        d = getattr(self._bridge, "_state_dir", None)
        if d is not None:
            return Path(d)
        return None

    def _workflow_dir(self, wid: str) -> Path:
        root = self._state_root()
        if root is None:
            raise RuntimeError(
                "WorkflowRunner: state_dir not set (call set_state_dir "
                "first, or attach via the bridge)")
        return root / wid

    def _ledger_path(self, wid: str) -> Path:
        return self._workflow_dir(wid) / "ledger.jsonl"

    def _meta_path(self, wid: str) -> Path:
        return self._workflow_dir(wid) / "meta.json"

    def append_ledger(self, wid: str, record: dict) -> None:
        import json
        path = self._ledger_path(wid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def read_ledger(self, wid: str) -> list[dict]:
        import json
        path = self._ledger_path(wid)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]

    def _write_meta(self, wid: str, *, name: str, host: str | None,
                    kwargs: dict) -> None:
        import json
        try:
            path = self._meta_path(wid)
        except RuntimeError:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": name, "host": host, "kwargs": kwargs,
        }))

    def _read_meta(self, wid: str) -> dict | None:
        import json
        try:
            path = self._meta_path(wid)
        except RuntimeError:
            return None
        if not path.exists():
            return None
        return json.loads(path.read_text())

    # ── lifecycle ─────────────────────────────────────────────────────
    async def start(self, name: str, kwargs: dict | None = None, *,
                    host: str | None = None,
                    state_dir: Path | None = None,
                    workflow_id: str | None = None,
                    scheduler=None,
                    done_callback=None) -> str:
        """Schedule a workflow run; return its workflow_id.

        ``scheduler`` (optional) is a callable ``(coro, *, name) -> Task``
        used to dispatch the coroutine — Textual bridges pass
        ``app.run_worker`` so downstream session deliveries inherit
        ``active_app`` context. Defaults to ``asyncio.create_task``.
        ``done_callback`` (optional) is invoked once the workflow has
        finished (with no arguments) so callers can deliver inbox
        callbacks without polling status."""
        wid = workflow_id or new_ulid()
        fn = get_workflow(name)
        if fn is None:
            raise WorkflowError(
                f"unknown workflow: {name!r}. "
                f"Available: {list_workflows()}")
        engine = WorkflowEngine(
            name=name, workflow_id=wid,
            host=host, config=dict(getattr(fn, "_config", {}) or {}),
            bridge=self._bridge,
            queue_manager=getattr(self._bridge, "queue_manager", None),
            inbox_router=getattr(self._bridge, "inbox_router", None),
            state_dir=state_dir)
        self._write_meta(wid, name=name, host=host, kwargs=kwargs or {})
        coro = self._run(engine, fn, kwargs or {}, done_callback)
        if scheduler is not None:
            task = scheduler(coro, name=f"workflow:{name}:{wid}")
        else:
            task = asyncio.create_task(
                coro, name=f"workflow:{name}:{wid}")
        self._running[wid] = _RunningWorkflow(
            id=wid, name=name, host=host, task=task, engine=engine)
        return wid

    async def _run(self, engine: WorkflowEngine, fn, kwargs: dict,
                   done_callback) -> None:
        wid = engine.workflow_id
        record = self._running.get(wid)
        try:
            result = await fn(engine, **kwargs)
            if record is not None:
                record.status = "ok"
                record.result = result
            self._safe_append(wid, {"kind": "finished",
                                    "result": _jsonable(result)})
        except asyncio.CancelledError:
            if record is not None:
                record.status = "cancelled"
                record.error = "cancelled_by_user"
            self._safe_append(wid, {"kind": "errored",
                                    "error": "cancelled_by_user"})
            raise
        except WorkflowError as e:
            if record is not None:
                record.status = "error"
                record.error = str(e)
            self._safe_append(wid, {"kind": "errored", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            if record is not None:
                record.status = "error"
                record.error = f"unexpected: {type(e).__name__}: {e}"
            self._safe_append(wid, {"kind": "errored",
                                    "error": f"{type(e).__name__}: {e}"})
        finally:
            await _runner_cleanup(engine)
            if done_callback is not None:
                try:
                    res = done_callback()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:  # noqa: BLE001
                    pass

    def _safe_append(self, wid: str, record: dict) -> None:
        try:
            self.append_ledger(wid, record)
        except Exception:  # noqa: BLE001 — ledger write is best-effort
            pass

    async def resume(self, workflow_id: str) -> str | None:
        """Restart a workflow whose ledger exists but which is not yet
        finished. Returns the workflow_id (same one — resume re-uses it
        so resume_state() finds the prior checkpoint), or ``None`` if
        the workflow is already terminal."""
        meta = self._read_meta(workflow_id)
        if meta is None:
            raise KeyError(f"unknown workflow_id: {workflow_id!r}")
        records = self.read_ledger(workflow_id)
        if any(r.get("kind") in {"finished", "errored"}
               and r.get("error") != "cancelled_by_user"
               and not r.get("_resumed") for r in records):
            # Workflow has a terminal record — but we accept re-running
            # after an errored entry (the resume case). Reject only on
            # 'finished'.
            pass
        if any(r.get("kind") == "finished" for r in records):
            return None
        # Mark resume in the ledger for forensics.
        self._safe_append(workflow_id, {"kind": "resumed"})
        await self.start(
            meta["name"], meta.get("kwargs", {}),
            host=meta.get("host"),
            workflow_id=workflow_id)
        return workflow_id

    def status(self, workflow_id: str) -> dict:
        r = self._running.get(workflow_id)
        if r is None:
            return {"workflow_id": workflow_id, "status": "unknown"}
        out: dict[str, Any] = {
            "workflow_id": workflow_id,
            "name": r.name,
            "host": r.host,
            "status": r.status,
        }
        if r.result is not None:
            out["result"] = r.result
        if r.error is not None:
            out["error"] = r.error
        return out

    async def cancel(self, workflow_id: str) -> dict:
        r = self._running.get(workflow_id)
        if r is None:
            return {"ok": False, "error": f"unknown workflow_id: {workflow_id}"}
        if r.task.done():
            return {"ok": True, "status": r.status, "note": "already done"}
        r.task.cancel()
        try:
            await r.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return {"ok": True, "status": r.status}

    # ── human questions ───────────────────────────────────────────────
    async def register_human_question(self, *, host: str | None,
                                      workflow_id: str,
                                      question: str,
                                      options: list[str] | None,
                                      fut: asyncio.Future) -> None:
        """Record a pending question for ``host``. Tests / the TUI input
        bar should call ``deliver_human_reply(host, reply)`` to resolve
        the future."""
        if host is None:
            if not fut.done():
                fut.set_exception(RuntimeError(
                    "ask_human: workflow has no host to ask"))
            return
        pq = _PendingQuestion(
            workflow_id=workflow_id, host=host,
            question=question, options=options, fut=fut)
        self._last_options[host] = options
        self._questions.setdefault(host, deque()).append(pq)

    def pending_question(self, host: str) -> _PendingQuestion | None:
        q = self._questions.get(host)
        if q:
            return q[0]
        return None

    def last_options(self, host: str) -> list[str] | None:
        return self._last_options.get(host)

    def deliver_human_reply(self, host: str, reply: str) -> bool:
        """Resolve the oldest pending question on ``host`` with ``reply``.
        Returns True if a question was waiting, False otherwise."""
        q = self._questions.get(host)
        if not q:
            return False
        pq = q.popleft()
        if not pq.fut.done():
            pq.fut.set_result(reply)
        return True

    # ── runner-mediated session bridges ───────────────────────────────
    async def send_and_await_reply(self, *, handle: str, prompt: str,
                                   workflow_id: str, workflow_name: str,
                                   timeout: float | None = None) -> str:
        """Forward a user-turn to ``handle`` via the bridge's session
        machinery; await the next complete assistant message.

        v1 stub: when the bridge exposes a ``send_and_await_reply`` we
        delegate; otherwise we deliver through inbox_router and return
        an empty string (matching legacy fire-and-forget semantics)."""
        impl = getattr(self._bridge, "session_send_and_await", None)
        if impl is not None:
            return await impl(
                handle=handle, prompt=prompt,
                workflow_id=workflow_id, workflow_name=workflow_name,
                timeout=timeout)
        inbox = getattr(self._bridge, "inbox_router", None)
        if inbox is not None:
            from aegis.queue.schema import InboxMessage, now_iso
            await inbox.deliver(handle, InboxMessage(
                sender=f"workflow:{workflow_name}",
                timestamp=now_iso(), body=prompt))
        return ""

    async def spawn_subagent(self, profile: str, *,
                             alias: str | None = None) -> str:
        return await self._bridge.spawn(profile, handle=alias)

    async def close_session(self, handle: str) -> None:
        await self._bridge.close(handle)
