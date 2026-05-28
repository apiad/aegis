from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from aegis.drivers.base import HarnessSession
from aegis.events import Event, Result, ToolResult, ToolUse
from aegis.queue.schema import InboxMessage, render_inbox_header
from aegis.tui.metrics import SessionMetrics, context_window_for
from aegis.tui.state import AgentState

log = logging.getLogger("aegis.core.session")

EventCb = Callable[["AgentSession", Event], None]
StateCb = Callable[["AgentSession", AgentState, bool], None]
InboxCb = Callable[["AgentSession", InboxMessage], None]
CloseCb = Callable[["AgentSession", str], None]


def _render_batch(batch: list[InboxMessage]) -> str:
    return "\n\n".join(
        render_inbox_header(m) + "\n" + m.body for m in batch)


class AgentSession:
    """One harness conversation, frontend-agnostic. Observers render."""

    def __init__(self, session: HarnessSession, agent, agent_slug: str,
                 handle: str, *,
                 now: Callable[[], float] = time.monotonic,
                 inbox=None,
                 opening_prompt: str | None = None) -> None:
        self._session = session
        self.agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self.state = AgentState.ready
        _harness = getattr(agent, "harness", "")
        _model = getattr(agent, "model", "")
        self.metrics = SessionMetrics(
            context_window=context_window_for(_harness, _model),
            provider=_harness,
            model=_model)
        self._now = now
        self._started = False
        self._task: asyncio.Task | None = None
        self._inbox = inbox                       # InboxRouter | None
        self._inbox_buffer: list[InboxMessage] = []
        self._opening_prompt = opening_prompt
        # Primary observers — the owning frontend (TUI pane, headless
        # SessionManager wrapper) sets these for its own renderer/state
        # tracking. Multi-observer slots below let extra subscribers
        # (e.g. QueueManager's completion watcher) chain in without
        # clobbering the primary.
        self.on_event: EventCb | None = None
        self.on_state: StateCb | None = None
        # Fired synchronously at the top of deliver() for every incoming
        # inbox message — fires whether the session is idle (dispatches
        # immediately) or mid-turn (buffers for chain). Lets frontends
        # surface "received from <sender>" before the agent reacts.
        self.on_inbox: InboxCb | None = None
        self.on_close: CloseCb | None = None
        self._extra_event_observers: list[EventCb] = []
        self._extra_state_observers: list[StateCb] = []
        self._extra_inbox_observers: list[InboxCb] = []
        self._extra_close_observers: list[CloseCb] = []
        # Captured by _run_turn's except clause for postmortem inspection.
        # None until a harness error occurs; replaced on each new error.
        self.last_error: Exception | None = None

    @property
    def session_id(self) -> str | None:
        return self._session.session_id

    def add_event_observer(self, cb: EventCb) -> None:
        """Subscribe an additional event callback. Fires after on_event."""
        self._extra_event_observers.append(cb)

    def add_state_observer(self, cb: StateCb) -> None:
        """Subscribe an additional state callback. Fires after on_state."""
        self._extra_state_observers.append(cb)

    def add_inbox_observer(self, cb: InboxCb) -> None:
        """Subscribe an additional inbox callback. Fires after on_inbox."""
        self._extra_inbox_observers.append(cb)

    def add_close_observer(self, cb: CloseCb) -> None:
        """Subscribe an additional close callback. Fires after on_close."""
        self._extra_close_observers.append(cb)

    def _emit_close(self, reason: str) -> None:
        if self.on_close is not None:
            try:
                self.on_close(self, reason)
            except Exception:
                log.exception("on_close raised; continuing")
        for cb in self._extra_close_observers:
            try:
                cb(self, reason)
            except Exception:
                log.exception("close observer raised; continuing")

    def _emit_state(self, state: AgentState, *, finished: bool) -> None:
        self.state = state
        if self.on_state is not None:
            self.on_state(self, state, finished)
        for cb in self._extra_state_observers:
            cb(self, state, finished)

    async def send(self, text: str) -> None:
        if self.state is AgentState.working:
            return
        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())
        self._task = asyncio.create_task(self._run_turn(text))

    async def deliver(self, msg: InboxMessage) -> None:
        """Push an inbox message at this session. Wake if idle; buffer
        if mid-turn; the turn-end hook will chain a follow-up turn."""
        if self.on_inbox is not None:
            try:
                self.on_inbox(self, msg)
            except Exception:
                log.exception("on_inbox raised; continuing")
        for cb in self._extra_inbox_observers:
            try:
                cb(self, msg)
            except Exception:
                log.exception("inbox observer raised; continuing")
        self._inbox_buffer.append(msg)
        if self.state is AgentState.working:
            return
        # idle: drain everything we hold and wake
        batch = self._inbox_buffer
        self._inbox_buffer = []
        text = _render_batch(batch)
        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())
        self._task = asyncio.create_task(self._run_turn(text))

    async def _run_turn(self, text: str) -> None:
        saw_result = False
        try:
            if not self._started:
                await self._session.start()
                self._started = True
                self.metrics.begin_session(self._now())
            await self._session.send(text)
            async for ev in self._session.events():
                if self.on_event is not None:
                    self.on_event(self, ev)
                for cb in self._extra_event_observers:
                    cb(self, ev)
                if isinstance(ev, ToolUse):
                    self.metrics.record_tool()
                elif isinstance(ev, ToolResult) and ev.is_error:
                    self.metrics.record_tool_error()
                if isinstance(ev, Result):
                    self.metrics.commit(ev.usage, self._now())
                    saw_result = True
                    self._emit_state(
                        AgentState.error if ev.is_error else AgentState.ready,
                        finished=True)
                else:
                    u = getattr(ev, "usage", None)
                    if u is not None:
                        self.metrics.observe(u)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — harness failure → error
            # Capture the exception so observers / tests can introspect
            # what actually broke. Also log to stderr with traceback;
            # the renderer's "⚠ harness error" line is just a marker.
            import sys
            import traceback
            self.last_error = e
            print(f"[aegis] {self.handle} harness error: "
                  f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
            traceback.print_exception(e, file=sys.stderr)
            if not saw_result:
                self.metrics.commit(None, self._now())
                self._emit_state(AgentState.error, finished=True)
            self._chain_if_pending()
            return
        if not saw_result:
            self.metrics.commit(None, self._now())
            self._emit_state(AgentState.error, finished=True)
        self._chain_if_pending()

    def _chain_if_pending(self) -> None:
        if not self._inbox_buffer:
            return
        batch = self._inbox_buffer
        self._inbox_buffer = []
        text = _render_batch(batch)
        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())
        self._task = asyncio.create_task(self._run_turn(text))

    async def interrupt(self) -> None:
        if self.state is not AgentState.working:
            return
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.metrics.cancel_turn(self._now())
        self._emit_state(AgentState.ready, finished=False)

    async def close(self, reason: str = "explicit") -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._started:
            await self._session.close()
        self._emit_close(reason)
