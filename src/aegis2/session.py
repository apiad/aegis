"""One Claude Code session: its child process, its store, its fold, its status.

The session is a continuous fold. Every stdout line is stored, parsed once,
folded into entry patches and published, whatever the turn state; an
interrupt only writes a ``control_request`` and its ``result`` arrives through
the same path.

Status: ``starting`` until the child runs, then ``idle``. Only a sent prompt
or a turn-bearing event moves it to ``working``, and only ``result`` moves it
back. System notices never do (DESIGN.md, "System notices never start a
turn"). ``error`` means the child died or ignored an interrupt; ``closed`` is
final.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .claude.process import ClaudeProcess
from .claude.stream import TURN_BEARING, Init, Result, parse
from .transcript.entries import Fold
from .transcript.store import Store

Publish = Callable[[str, list[dict]], None]


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
        log_id: str,
        spec: SpawnSpec,
        store: Store,
        argv: list[str],
        stderr_path: Path,
        publish: Publish,
        interrupt_timeout: float = 10.0,
    ) -> None:
        self.log_id = log_id
        self.spec = spec
        self.store = store
        self.channel = f"transcript:{log_id}"
        self._publish = publish
        self._interrupt_timeout = interrupt_timeout
        self._fold = Fold()
        self._proc = ClaudeProcess(
            argv, spec.cwd, stderr_path, self._on_line, self._on_exit
        )
        self._closing = False
        self._interrupt_timer: asyncio.Task | None = None
        self.status = "starting"
        self.model_id: str | None = None
        self.cost_usd: float | None = None
        self.context_tokens: int | None = None
        self.context_window: int | None = None
        self.started_at = time.time()

    # -- what the outside sees -------------------------------------------
    def meta(self) -> dict:
        return {
            "log_id": self.log_id,
            "profile": self.spec.profile,
            "model": self.model_id or self.spec.model,
            "effort": self.spec.effort,
            "permission": self.spec.permission,
            "cwd": str(self.spec.cwd),
            "status": self.status,
            "cost_usd": self.cost_usd,
            "context_tokens": self.context_tokens,
            "context_window": self.context_window,
            "started_at": self.started_at,
        }

    def entries(self) -> list[dict]:
        return self._fold.entries()

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    # -- operations --------------------------------------------------------
    async def start(self) -> None:
        s = self.spec
        self._record(
            {
                "kind": "spawn",
                "profile": s.profile,
                "model": s.model,
                "effort": s.effort,
                "permission": s.permission,
                "cwd": str(s.cwd),
                "argv": self._proc.argv,
            }
        )
        await self._proc.start()
        self._set(status="idle")

    async def send(self, text: str) -> None:
        self._record({"kind": "send", "text": text})
        await self._proc.write(
            {"type": "user", "message": {"role": "user", "content": text}}
        )
        self._set(status="working")

    async def interrupt(self) -> None:
        if self.status != "working":
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

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._interrupt_timer:
            self._interrupt_timer.cancel()
        await self._proc.terminate()
        self._record({"kind": "close"})
        self._set(status="closed")
        self.store.close()

    # -- internals ---------------------------------------------------------
    async def _interrupt_deadline(self) -> None:
        await asyncio.sleep(self._interrupt_timeout)
        if self.status == "working" and not self._closing:
            self._record(
                {"kind": "interrupt_timeout", "after_s": self._interrupt_timeout}
            )
            self._set(status="error")

    def _record(self, record: dict, events: list | None = None) -> None:
        stored = self.store.append({"ts": time.time(), "src": "aegis2", **record})
        self._publish(self.channel, self._fold.apply(stored, events))

    def _set(self, **changes: object) -> None:
        changes = {k: v for k, v in changes.items() if getattr(self, k) != v}
        if not changes:
            return
        for k, v in changes.items():
            setattr(self, k, v)
        if "model_id" in changes:
            changes["model"] = changes.pop("model_id")
        self._publish("session", [{"set": changes}])

    def _on_line(self, line: str) -> None:
        if self._closing:
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
            if isinstance(ev, Init) and ev.model:
                changes["model_id"] = ev.model
            if isinstance(ev, Result):
                if self._interrupt_timer:
                    self._interrupt_timer.cancel()
                if (
                    self.status in ("working", "idle")
                    or changes.get("status") == "working"
                ):
                    changes["status"] = "idle"
                if ev.cost_usd is not None:
                    changes["cost_usd"] = ev.cost_usd
                if ev.context_window:
                    changes["context_window"] = ev.context_window
        if changes:
            self._set(**changes)

    def _on_exit(self, code: int, stderr_tail: list[str]) -> None:
        if self._closing:
            return
        self._record({"kind": "exit", "code": code, "stderr_tail": stderr_tail})
        self._set(status="error")
