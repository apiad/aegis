"""One Claude Code session: its store, its fold, its status, and a process when
it has one.

A session exists without a process. ``stopped`` means there is none; the first
prompt starts ``claude``, with ``--resume`` once Claude has given the session an
id, and the store and the fold simply continue (Claude prints nothing old on
resume, measured in issue #127). Only shutdown, Stop and Close end a process:
a session waiting on its own background task wakes itself, so nothing stops an
idle-looking one.

The session is a continuous fold. Every stdout line is stored, parsed once,
folded into entry patches and published, whatever the turn state. Status: only a
sent prompt or a turn-bearing event moves it to ``working``, and only ``result``
moves it back to ``idle``; system notices never do. ``error`` is an interrupt
that went unanswered. A child that exits leaves the session ``stopped``.

A clean server shutdown records nothing in the store. The meta keeps the last
status, and the registry marks a session that was ``working`` at its next boot,
so a crash and a clean stop read the same.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .claude.process import ClaudeProcess, build_argv
from .claude.stream import TURN_BEARING, Init, Result, parse
from .meta import MetaStore
from .names import default_title
from .transcript.entries import Fold, fold_records
from .transcript.store import Store, read_store

Publish = Callable[[str, list[dict]], None]

# What a fleet card shows; a change to any of these publishes the session.
_SHOWN = ("status", "activity", "cost_usd", "context_tokens", "context_window", "handle", "title", "model_id")


@dataclass(frozen=True)
class SpawnSpec:
    profile: str
    model: str
    effort: str
    permission: str
    cwd: Path


class Session:
    def __init__(
        self,
        *,
        log_id: str,
        spec: SpawnSpec,
        handle: str,
        store: Store,
        stderr_path: Path,
        claude_bin: str,
        publish: Publish,
        metas: MetaStore,
        title: str = "",
        claude_session_id: str | None = None,
        archived: bool = False,
        created_at: float | None = None,
        last_activity: float | None = None,
        last_status: str = "stopped",
        cost_usd: float | None = None,
        context_tokens: int | None = None,
        context_window: int | None = None,
        activity: str = "",
        model_id: str | None = None,
        interrupt_timeout: float = 10.0,
    ) -> None:
        self.log_id = log_id
        self.spec = spec
        self.handle = handle
        self.title = title
        self.store = store
        self.channel = f"transcript:{log_id}"
        self.claude_session_id = claude_session_id
        self.archived = archived
        self.created_at = created_at or time.time()
        self.last_activity = last_activity or self.created_at
        self.last_status = last_status
        self.cost_usd = cost_usd
        self.context_tokens = context_tokens
        self.context_window = context_window
        self.activity = activity
        self.model_id = model_id
        self.status = "stopped"
        self._stderr_path = stderr_path
        self._claude_bin = claude_bin
        self._publish = publish
        self._metas = metas
        self._interrupt_timeout = interrupt_timeout
        self._fold: Fold | None = None
        self._proc: ClaudeProcess | None = None
        self._stopping = False
        self._interrupt_timer: asyncio.Task | None = None

    # -- what the outside sees -------------------------------------------
    def meta(self) -> dict:
        """The persisted form (``<state>/sessions/<log_id>.json``)."""
        s = self.spec
        return {
            "log_id": self.log_id,
            "handle": self.handle,
            "title": self.title,
            "profile": s.profile,
            "model": s.model,
            "model_id": self.model_id,
            "effort": s.effort,
            "permission": s.permission,
            "cwd": str(s.cwd),
            "claude_session_id": self.claude_session_id,
            "archived": self.archived,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "last_status": self.status if self._proc else self.last_status,
            "cost_usd": self.cost_usd,
            "context_tokens": self.context_tokens,
            "context_window": self.context_window,
            "activity": self.activity,
        }

    def wire(self) -> dict:
        """What the ``sessions`` channel carries."""
        m = self.meta()
        m["state"] = self.status
        m["model"] = self.model_id or self.spec.model
        return m

    def fold(self) -> Fold:
        if self._fold is None:
            if self.store.path.exists():
                records, _ = read_store(self.store.path)
                self._fold = fold_records(records)
            else:
                self._fold = Fold()
        return self._fold

    def entries(self) -> list[dict]:
        return self.fold().entries()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    # -- operations --------------------------------------------------------
    async def start(self) -> None:
        """A brand-new session: record the spawn, then start ``claude``."""
        s = self.spec
        self._record(
            {
                "kind": "spawn",
                "profile": s.profile,
                "model": s.model,
                "effort": s.effort,
                "permission": s.permission,
                "cwd": str(s.cwd),
            }
        )
        await self.ensure_running()

    async def ensure_running(self) -> None:
        """Start ``claude`` if there is no process. Raises FileNotFoundError when
        the binary is missing, leaving the session stopped."""
        if self.running:
            return
        resume = self.claude_session_id
        argv = build_argv(self._claude_bin, self.spec.model, self.spec.effort, self.spec.permission, resume)
        proc = ClaudeProcess(argv, self.spec.cwd, self._stderr_path, self._on_line, self._on_exit)
        await proc.start()
        self._proc = proc
        self._stopping = False
        if resume:
            self._record({"kind": "resume", "claude_session_id": resume})
        self._set(status="idle")

    async def send(self, text: str) -> None:
        await self.ensure_running()
        assert self._proc is not None
        if not self.title:
            self._set(title=default_title(text))
        self._record({"kind": "send", "text": text})
        await self._proc.write({"type": "user", "message": {"role": "user", "content": text}})
        self._set(status="working")

    async def interrupt(self) -> None:
        if self.status != "working" or self._proc is None:
            return
        self._record({"kind": "interrupt"})
        await self._proc.write(
            {
                "type": "control_request",
                "request_id": f"aegis2_interrupt_{time.monotonic_ns()}",
                "request": {"subtype": "interrupt"},
            }
        )
        if self._interrupt_timer is None or self._interrupt_timer.done():
            self._interrupt_timer = asyncio.create_task(self._interrupt_deadline())

    async def stop(self) -> None:
        """End the process and keep the session."""
        if self._proc is None:
            return
        await self._end_process()
        self._record({"kind": "stop"})
        self._set(status="stopped")
        self._metas.write(self.meta())

    async def shutdown(self) -> None:
        """The server is stopping: end the process, record nothing, and keep the
        last status in the meta so a mid-turn session is marked at next boot."""
        if self._proc is not None:
            self.last_status = self.status
            await self._end_process()
        self.store.close()
        self._metas.write(self.meta())

    # -- internals ---------------------------------------------------------
    async def _end_process(self) -> None:
        if self._interrupt_timer:
            self._interrupt_timer.cancel()
        proc, self._proc = self._proc, None
        if proc is not None:
            self._stopping = True
            await proc.terminate()

    async def _interrupt_deadline(self) -> None:
        await asyncio.sleep(self._interrupt_timeout)
        if self.status == "working" and self._proc is not None:
            self._record({"kind": "interrupt_timeout", "after_s": self._interrupt_timeout})
            self._set(status="error")

    def _record(self, record: dict, events: list | None = None) -> None:
        fold = self.fold()
        stored = self.store.append({"ts": time.time(), "src": "aegis2", **record})
        self.last_activity = stored["ts"]
        self._publish(self.channel, fold.apply(stored, events))
        self._set(activity=fold.activity())

    def _set(self, **changes: object) -> None:
        changes = {k: v for k, v in changes.items() if getattr(self, k) != v}
        if not changes:
            return
        for k, v in changes.items():
            setattr(self, k, v)
        if "status" in changes:
            self.last_status = self.status
        if any(k in _SHOWN for k in changes):
            self._publish("sessions", [{"upsert": self.wire()}])
        if changes.get("status") in ("idle", "stopped", "error"):
            self._metas.write(self.meta())
        else:
            self._metas.write_soon(self.meta())

    def _on_line(self, line: str) -> None:
        if self._stopping:
            return
        events = parse(line)
        self._record({"src": "claude", "line": line}, events)
        changes: dict[str, object] = {}
        for ev in events:
            if isinstance(ev, TURN_BEARING) and self.status == "idle":
                changes["status"] = "working"
            usage = getattr(ev, "usage", None)
            if usage is not None and getattr(ev, "parent", None) is None:
                changes["context_tokens"] = usage.context
            if isinstance(ev, Init):
                if ev.model:
                    changes["model_id"] = ev.model
                if ev.session_id:
                    changes["claude_session_id"] = ev.session_id
            if isinstance(ev, Result):
                if self._interrupt_timer:
                    self._interrupt_timer.cancel()
                changes["status"] = "idle"
                if ev.cost_usd is not None:
                    changes["cost_usd"] = ev.cost_usd
                if ev.context_window:
                    changes["context_window"] = ev.context_window
        if changes:
            self._set(**changes)

    def _on_exit(self, code: int, stderr_tail: list[str]) -> None:
        if self._stopping:
            return
        self._proc = None
        self._record({"kind": "exit", "code": code, "stderr_tail": stderr_tail})
        self._set(status="stopped")
