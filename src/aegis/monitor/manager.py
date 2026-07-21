"""MonitorManager — poll agent-supplied bash, wake the agent on the outcome.

aegis does not own the watched process; the agent launches it (or it already
runs, e.g. a dev server). Each ``interval_s`` the manager evaluates the
monitor's bash in the session cwd: ``progress`` (echoes 0–100) updates the
bar/ETA, ``fail`` (exit 0) is a terminal failure, ``done`` (exit 0) is terminal
success. On any terminal state — including a ``timeout_s`` backstop — the agent
is woken via an inbox callback, interrupting its current turn if it is busy so
the notice lands immediately.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

from aegis.monitor.schema import (
    CANCELLED,
    DONE,
    FAILED,
    TIMED_OUT,
    WATCHING,
    Monitor,
    MonitorView,
    eta_seconds,
    parse_pct,
    terminal_label,
)
from aegis.queue.schema import InboxMessage, new_ulid, now_iso, sender_monitor

# (cmd, cwd) -> (exit_code, stdout)
RunBash = Callable[[str, "str | None"], Awaitable[tuple[int, str]]]


async def _default_run_bash(cmd: str, cwd: str | None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd, cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return (proc.returncode or 0), out.decode(errors="replace")


class MonitorManager:
    def __init__(self, inbox_router, session_manager=None, *,
                 run_bash: RunBash | None = None,
                 clock: Callable[[], float] | None = None,
                 now: Callable[[], str] = now_iso) -> None:
        self._inbox = inbox_router
        self._sm = session_manager
        self._run_bash = run_bash or _default_run_bash
        self._clock = clock or time.monotonic
        self._now = now
        self._monitors: dict[str, Monitor] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._subs: list[Callable[[], None]] = []

    # ----- observation (drives the TUI strip) -----------------------
    def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._subs.append(cb)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._subs.remove(cb)
        return _unsub

    def _notify(self) -> None:
        for cb in list(self._subs):
            with contextlib.suppress(Exception):
                cb()

    def snapshot(self) -> list[MonitorView]:
        """Live monitors only (terminal ones drop off the strip)."""
        now = self._clock()
        return [
            MonitorView(
                id=m.id, description=m.description, state=m.state,
                pct=m.pct, eta_s=m.eta_s, elapsed_s=now - m.started_at)
            for m in self._monitors.values() if m.state == WATCHING
        ]

    def status(self, monitor_id: str) -> dict | None:
        m = self._monitors.get(monitor_id)
        if m is None:
            return None
        return {"id": m.id, "description": m.description, "state": m.state,
                "pct": m.pct, "eta_s": m.eta_s}

    def list_monitors(self) -> list[dict]:
        return [self.status(mid) for mid in self._monitors]

    # ----- lifecycle -------------------------------------------------
    def start_monitor(self, *, from_handle: str, description: str, done: str,
                      fail: str | None = None, progress: str | None = None,
                      cwd: str | None = None, interval_s: float = 2.0,
                      timeout_s: float = 3600.0, autorun: bool = True) -> str:
        mid = new_ulid()
        self._monitors[mid] = Monitor(
            id=mid, from_handle=from_handle, description=description,
            done=done, fail=fail, progress=progress, cwd=cwd,
            interval_s=interval_s, timeout_s=timeout_s,
            started_at=self._clock())
        self._notify()
        if autorun:
            self._tasks[mid] = asyncio.create_task(self._run(mid))
        return mid

    async def _run(self, mid: str) -> None:
        try:
            while True:
                mon = self._monitors.get(mid)
                if mon is None or mon.state != WATCHING:
                    return
                await asyncio.sleep(mon.interval_s)
                await self.tick(mid)
        finally:
            self._tasks.pop(mid, None)

    async def tick(self, mid: str) -> None:
        """Evaluate one poll cycle. Public for deterministic testing."""
        mon = self._monitors.get(mid)
        if mon is None or mon.state != WATCHING:
            return
        elapsed = self._clock() - mon.started_at
        if elapsed >= mon.timeout_s:
            await self._finalize(mid, TIMED_OUT)
            return
        if mon.progress:
            code, out = await self._run_bash(mon.progress, mon.cwd)
            if code == 0:
                pct = parse_pct(out)
                if pct is not None:
                    mon.pct = pct
                    mon.eta_s = eta_seconds(pct, elapsed)
        if mon.fail:
            code, _ = await self._run_bash(mon.fail, mon.cwd)
            if code == 0:
                await self._finalize(mid, FAILED)
                return
        code, _ = await self._run_bash(mon.done, mon.cwd)
        if code == 0:
            await self._finalize(mid, DONE)
            return
        self._notify()

    async def cancel(self, mid: str) -> dict:
        mon = self._monitors.get(mid)
        if mon is None:
            return {"ok": False, "error": f"unknown monitor {mid!r}"}
        if mon.state != WATCHING:
            return {"ok": True, "state": mon.state, "note": "already terminal"}
        await self._finalize(mid, CANCELLED, notify_agent=False)
        task = self._tasks.pop(mid, None)
        if task is not None:
            task.cancel()
        return {"ok": True, "state": CANCELLED}

    def reap(self, handle: str) -> None:
        """Cancel a dead session's live monitors (called on session close)."""
        for mid, mon in list(self._monitors.items()):
            if mon.from_handle == handle and mon.state == WATCHING:
                mon.state = CANCELLED
                mon.ended_at = self._clock()
                task = self._tasks.pop(mid, None)
                if task is not None:
                    task.cancel()
        self._notify()

    async def _finalize(self, mid: str, state: str, *,
                        notify_agent: bool = True) -> None:
        mon = self._monitors[mid]
        mon.state = state
        mon.ended_at = self._clock()
        if state == DONE:
            mon.pct, mon.eta_s = 100.0, 0.0
        self._notify()
        if notify_agent:
            await self._deliver(mon)

    async def _deliver(self, mon: Monitor) -> None:
        elapsed = int((mon.ended_at or self._clock()) - mon.started_at)
        body = f"{mon.description} — {terminal_label(mon.state)} ({elapsed}s)"
        msg = InboxMessage(
            sender=sender_monitor(mon.id[-4:]),
            timestamp=self._now(),
            body=body,
            task_id=mon.id,
            status=("ok" if mon.state == DONE else "error"))
        # Interrupt only a busy agent (idle ones are woken by deliver alone);
        # either way the notice lands as the agent's next turn — immediately.
        if self._target_working(mon.from_handle):
            with contextlib.suppress(Exception):
                await self._sm.interrupt(mon.from_handle)
        await self._inbox.deliver(mon.from_handle, msg)

    def _target_working(self, handle: str) -> bool:
        """True only when the agent is running a *real* turn we should cut.

        A ``working`` state that is an unsolicited-turn drain (the harness
        processing its OWN background-task notification, e.g. a Claude
        ``run_in_background`` bash finishing) must NOT be interrupted:
        cutting it mid-resume wedges the wake behind an extra replay cycle.
        Deliver-only lets the notice land as a queued follow-up turn.
        """
        if self._sm is None:
            return False
        try:
            for s in self._sm.list_sessions():
                if getattr(s, "handle", None) == handle:
                    if getattr(s, "state", None) != "working":
                        return False
                    return not getattr(s, "unsolicited", False)
        except Exception:  # noqa: BLE001
            return False
        return False
