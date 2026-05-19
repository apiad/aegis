from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from aegis.drivers.base import HarnessSession
from aegis.events import Event, Result, ToolResult, ToolUse
from aegis.tui.metrics import SessionMetrics
from aegis.tui.state import AgentState

EventCb = Callable[["AgentSession", Event], None]
StateCb = Callable[["AgentSession", AgentState, bool], None]


class AgentSession:
    """One harness conversation, frontend-agnostic. Observers render."""

    def __init__(self, session: HarnessSession, agent, agent_slug: str,
                 handle: str, *,
                 now: Callable[[], float] = time.monotonic) -> None:
        self._session = session
        self.agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self.state = AgentState.ready
        self.metrics = SessionMetrics()
        self._now = now
        self._started = False
        self._task: asyncio.Task | None = None
        self.on_event: EventCb | None = None
        self.on_state: StateCb | None = None

    def _emit_state(self, state: AgentState, *, finished: bool) -> None:
        self.state = state
        if self.on_state is not None:
            self.on_state(self, state, finished)

    async def send(self, text: str) -> None:
        if self.state is AgentState.working:
            return
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
        except Exception:  # noqa: BLE001 - harness failure surfaces as error
            if not saw_result:
                self.metrics.commit(None, self._now())
                self._emit_state(AgentState.error, finished=True)
            return
        if not saw_result:
            self.metrics.commit(None, self._now())
            self._emit_state(AgentState.error, finished=True)

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

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._started:
            await self._session.close()
