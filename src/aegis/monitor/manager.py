"""MonitorManager — poll agent-supplied bash, wake the agent on the outcome.

aegis does not own the watched process; the agent launches it (or it already
runs, e.g. a dev server). Each ``interval_s`` the manager evaluates the
monitor's bash in the session cwd: ``progress`` (echoes 0–100) updates the
bar/ETA, ``fail`` (exit 0) is a terminal failure, ``done`` (exit 0) is terminal
success. On any terminal state — including a ``timeout_s`` backstop — the agent
is woken via an inbox callback: immediately when it is idle, otherwise buffered
and chained at its next turn boundary. Pass ``interrupt=True`` to cut a busy
agent's turn instead of waiting for it.
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
    condition_error,
    eta_seconds,
    parse_pct,
    roster_block,
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

    def snapshot(self, *, for_handle: str | None = None) -> list[MonitorView]:
        """Live monitors only (terminal ones drop off the strip).

        ``for_handle`` scopes the view to monitors created by that session,
        so a per-tab strip shows only its own monitors. ``None`` returns
        every live monitor.
        """
        now = self._clock()
        return [
            MonitorView(
                id=m.id, description=m.description, state=m.state,
                pct=m.pct, eta_s=m.eta_s, elapsed_s=now - m.started_at)
            for m in self._monitors.values()
            if m.state == WATCHING
            and (for_handle is None or m.from_handle == for_handle)
        ]

    def status(self, monitor_id: str) -> dict | None:
        m = self._monitors.get(monitor_id)
        if m is None:
            return None
        return {"id": m.id, "description": m.description, "state": m.state,
                "pct": m.pct, "eta_s": m.eta_s}

    def list_monitors(self, *, for_handle: str | None = None) -> list[dict]:
        return [self.status(m.id) for m in self._monitors.values()
                if for_handle is None or m.from_handle == for_handle]

    def roster(self, handle: str, *, exclude: str | None = None) -> list[dict]:
        """The live monitors ``handle`` still owns — the anti-stale roster.

        Agents forget monitors. A process gets killed by a PID sweep or
        superseded by a newer run, and its monitor keeps watching for a marker
        that can never arrive; the agent notices only when the operator points
        at it from outside. So the roster is surfaced at the two moments the
        agent is already looking at monitors — when it arms one, and when one
        wakes it — where the orphan sits right next to the thing it cares
        about instead of needing a deliberate ``aegis_monitors()`` call it has
        no reason to make.
        """
        now = self._clock()
        return [
            {"id": m.id, "description": m.description, "pct": m.pct,
             "elapsed_s": int(now - m.started_at)}
            for m in self._monitors.values()
            if m.state == WATCHING and m.from_handle == handle
            and m.id != exclude
        ]

    # ----- lifecycle -------------------------------------------------
    def start_monitor(self, *, from_handle: str, description: str, done: str,
                      fail: str | None = None, progress: str | None = None,
                      cwd: str | None = None, interval_s: float = 2.0,
                      timeout_s: float = 3600.0, interrupt: bool = False,
                      autorun: bool = True) -> str:
        # Refuse a condition that can never trip. Checked here, not only at
        # the MCP surface, so no caller can route around it.
        for cond in (done, fail, progress):
            err = condition_error(cond)
            if err is not None:
                raise ValueError(err)
        # And refuse a wake that can never be delivered — the same defect one
        # field over. `_fire` delivers to `from_handle`; point it at a handle
        # no session answers to and the monitor polls for its whole timeout,
        # trips, delivers into the void, and the agent waits forever.
        #
        # This is not an exotic mistake. An agent is never told when the
        # operator renames it — no message announces it, and its system prompt
        # still carries the handle it was born with — so it goes on passing
        # the name it remembers. The substrate is the only layer that can see
        # the mismatch, so it says so here rather than at the MCP surface,
        # where a caller could route around it.
        live = self._live_handles()
        if live and from_handle not in live:
            raise ValueError(
                f"no live session {from_handle!r} to wake — the monitor "
                f"would watch, trip, and deliver to nobody. Live handles: "
                f"{', '.join(sorted(live))}. If you were renamed, "
                f"aegis_list_sessions carries your current handle.")
        mid = new_ulid()
        self._monitors[mid] = Monitor(
            id=mid, from_handle=from_handle, description=description,
            done=done, fail=fail, progress=progress, cwd=cwd,
            interval_s=interval_s, timeout_s=timeout_s, interrupt=interrupt,
            started_at=self._clock())
        self._notify()
        # The monitor is now the authoritative waker for this handle: hold
        # back the harness's own spontaneous-event promotion so it can't
        # fire a competing wake for the same process.
        self._hold_session(from_handle)
        if autorun:
            self._tasks[mid] = asyncio.create_task(self._run(mid))
        return mid

    def _live_handles(self) -> set[str]:
        """The handles a wake could actually reach.

        Empty means "cannot say" as much as it means "nothing alive" — a
        manager reporting zero sessions is a stub or a boot-time race, and
        in production the arming session is itself live. Callers treat an
        empty set as no answer rather than as a veto.
        """
        if self._sm is None:
            return set()
        try:
            return {h for s in self._sm.list_sessions()
                    if (h := getattr(s, "handle", None)) is not None}
        except Exception:  # noqa: BLE001 — a manager that cannot answer
            return set()   # must not veto the monitor

    def _session_for(self, handle: str):
        get = getattr(self._sm, "get", None)
        return get(handle) if callable(get) else None

    def _hold_session(self, handle: str) -> None:
        sess = self._session_for(handle)
        hold = getattr(sess, "hold_unsolicited", None)
        if callable(hold):
            with contextlib.suppress(Exception):
                hold()

    def _release_session(self, handle: str) -> None:
        sess = self._session_for(handle)
        release = getattr(sess, "release_unsolicited", None)
        if callable(release):
            with contextlib.suppress(Exception):
                release()

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
        """Stop a monitor, and say plainly what died and what is still alive.

        No inbox callback — the agent asked for this, so waking it about its
        own decision is noise. The tool result carries the acknowledgement
        instead: the description of what was cancelled (ULIDs differ in a few
        characters, so ``{ok: true}`` alone asks the agent to take on faith
        that it hit the one it meant) and the roster of what remains. Cancel
        is when an agent is pruning, which makes it the best moment of the
        three to show the rest of the pile.
        """
        mon = self._monitors.get(mid)
        if mon is None:
            return {"ok": False, "error": f"unknown monitor {mid!r}"}
        if mon.state != WATCHING:
            return {"ok": True, "state": mon.state,
                    "description": mon.description,
                    "note": f"already terminal ({terminal_label(mon.state)}) "
                            "— nothing to cancel"}
        await self._finalize(mid, CANCELLED, notify_agent=False)
        task = self._tasks.pop(mid, None)
        if task is not None:
            task.cancel()
        rest = self.roster(mon.from_handle)
        return {"ok": True, "state": CANCELLED,
                "description": mon.description,
                "still_watching": rest,
                "note": (f"cancelled — you have {len(rest)} live monitor"
                         f"{'' if len(rest) == 1 else 's'} left"
                         if rest else
                         "cancelled — you now have no monitors running")}

    def rename(self, handle: str, new_handle: str) -> None:
        """Follow a session that renamed itself.

        A monitor is keyed by the handle that armed it — that key scopes the
        per-tab strip and addresses the wake at the end. Left on the old
        name, the tab stops showing the monitor and its callback is
        delivered to a handle nobody answers to.
        """
        if handle == new_handle:
            return
        moved = False
        for m in self._monitors.values():
            if m.from_handle == handle:
                m.from_handle = new_handle
                moved = True
        if moved:
            self._notify()

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
        # Terminal: stop holding back the harness's spontaneous-event
        # promotion. Deliver first (so the wake is queued) then release —
        # on the last release the session re-arms its idle watcher, which
        # would otherwise claim the queued events as their own turn.
        if notify_agent:
            await self._deliver(mon)
        self._release_session(mon.from_handle)

    async def _deliver(self, mon: Monitor) -> None:
        elapsed = int((mon.ended_at or self._clock()) - mon.started_at)
        # _finalize() has already marked this one terminal, so it drops out of
        # its own roster.
        body = (f"{mon.description} — {terminal_label(mon.state)} ({elapsed}s)"
                + roster_block(self.roster(mon.from_handle)))
        msg = InboxMessage(
            sender=sender_monitor(mon.id[-4:]),
            timestamp=self._now(),
            body=body,
            task_id=mon.id,
            status=("ok" if mon.state == DONE else "error"))
        # Default: deliver only. A busy agent is very often still finishing
        # the very turn that armed this monitor, and cutting that turn throws
        # its tail away to buy nothing — the notice is buffered and chained at
        # the turn boundary anyway. Opt in with interrupt=True when the news
        # genuinely can't wait for the current turn to end.
        if mon.interrupt and self._target_working(mon.from_handle):
            with contextlib.suppress(Exception):
                await self._sm.interrupt(mon.from_handle, drain=False)
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
