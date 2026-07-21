from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from aegis.drivers.base import HarnessSession
from aegis.events import AssistantText, Event, Result, ToolResult, ToolUse
from aegis.hooks import (
    PostTurnEvent, PreTurnContext, PreTurnResult, SessionEndEvent,
    SessionHandle, SessionStartEvent, Turn,
)
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG
from aegis.hooks.runner import run_observer_hooks, run_pre_turn_hooks
from aegis.queue.schema import Delivery, InboxMessage, render_inbox_header
from aegis.tui.metrics import SessionMetrics, context_window_for
from aegis.tui.state import AgentState

log = logging.getLogger("aegis.core.session")

EventCb = Callable[["AgentSession", Event], None]
StateCb = Callable[["AgentSession", AgentState, bool], None]
InboxCb = Callable[["AgentSession", InboxMessage], None]
DispatchCb = Callable[["AgentSession", list[InboxMessage]], None]
CloseCb = Callable[["AgentSession", str], None]


def _render_batch(batch: list[InboxMessage]) -> str:
    def _one(m: InboxMessage) -> str:
        header = render_inbox_header(m)
        # User text-box messages render headerless (plain user turn); inbox
        # messages keep their `> from …` substrate header.
        return f"{header}\n{m.body}" if header else m.body
    return "\n\n".join(_one(m) for m in batch)


class AgentSession:
    """One harness conversation, frontend-agnostic. Observers render."""

    def __init__(self, session: HarnessSession, agent, agent_slug: str,
                 handle: str, *,
                 now: Callable[[], float] = time.monotonic,
                 inbox=None,
                 opening_prompt: str | None = None,
                 project_root: Path | None = None) -> None:
        self._session = session
        self.agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self.project_root = project_root or Path.cwd()
        # hooks log into .aegis/state relative to the project root
        self.state_dir = self.project_root / ".aegis" / "state"

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
        # Idle watcher: armed at turn-end when the harness supports
        # spontaneous between-turn events (e.g. Claude's background
        # Monitor). Polls has_pending_event() and promotes any arrivals
        # into an unsolicited turn. None when no watcher is currently
        # armed (also when the harness doesn't support idle events).
        self._idle_task: asyncio.Task | None = None
        self._idle_poll_seconds = 0.25
        # True only while an unsolicited-turn drain is in flight (the harness
        # emitting post-Result events on its own — a background-task
        # notification or Monitor firing). Lets the MonitorManager tell a
        # self-resolving drain apart from a real turn and avoid interrupting
        # it (which would wedge the wake behind an extra replay cycle).
        self._unsolicited = False
        # >0 while one or more aegis monitors are watching this handle. The
        # aegis monitor is the authoritative waker, so while held we do NOT
        # promote the harness's own spontaneous events (e.g. a Claude
        # background-task notification for the same process) into a competing
        # unsolicited turn — they stay queued and fold into the turn the
        # monitor's delivered message drives. Prevents the double-wake race.
        self._unsolicited_hold = 0
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
        # Fired the instant a buffered batch leaves the buffer to start a
        # turn (idle-drain or turn-end chain) — never for the plain
        # send() path. Lets frontends learn which queued messages are now
        # being sent (e.g. clear their chips, mount user lines).
        self.on_dispatch: DispatchCb | None = None
        self.on_close: CloseCb | None = None
        self._extra_event_observers: list[EventCb] = []
        self._extra_state_observers: list[StateCb] = []
        self._extra_inbox_observers: list[InboxCb] = []
        self._extra_dispatch_observers: list[DispatchCb] = []
        self._extra_close_observers: list[CloseCb] = []
        # Captured by _run_turn's except clause for postmortem inspection.
        # None until a harness error occurs; replaced on each new error.
        self.last_error: Exception | None = None
        # session_start hooks fire exactly once at the top of the first
        # _run_turn (before pre_turn). Flag is independent of _started
        # (which tracks harness-subprocess lifecycle) so the hook fires
        # even if the harness never successfully starts.
        self._session_start_fired = False

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

    def add_dispatch_observer(self, cb: DispatchCb) -> None:
        """Subscribe an additional dispatch callback. Fires after on_dispatch."""
        self._extra_dispatch_observers.append(cb)

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

    def _emit_dispatch(self, batch: list[InboxMessage]) -> None:
        if self.on_dispatch is not None:
            try:
                self.on_dispatch(self, batch)
            except Exception:
                log.exception("on_dispatch raised; continuing")
        for cb in self._extra_dispatch_observers:
            try:
                cb(self, batch)
            except Exception:
                log.exception("dispatch observer raised; continuing")

    def _emit_state(self, state: AgentState, *, finished: bool) -> None:
        self.state = state
        if self.on_state is not None:
            self.on_state(self, state, finished)
        for cb in self._extra_state_observers:
            cb(self, state, finished)

    @property
    def unsolicited_turn(self) -> bool:
        """Whether the current ``working`` turn is a self-resolving
        unsolicited drain rather than a real agent turn."""
        return self._unsolicited

    def hold_unsolicited(self) -> None:
        """Called when an aegis monitor starts watching this handle. While
        held, native spontaneous events are not promoted into their own
        turn — the monitor's delivered message is the authoritative wake."""
        self._unsolicited_hold += 1

    def release_unsolicited(self) -> None:
        """Called when a watching monitor reaches a terminal state. On the
        last release, re-arm the idle watcher so any events that queued up
        while held (and were never claimed by a monitor-driven turn) still
        drain rather than stranding until the next user send."""
        if self._unsolicited_hold > 0:
            self._unsolicited_hold -= 1
        if self._unsolicited_hold == 0 and self.state is not AgentState.working:
            self._arm_idle_watcher()

    async def send(self, text: str) -> None:
        if self.state is AgentState.working:
            return
        await self._cancel_idle_watcher()
        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())
        self._task = asyncio.create_task(self._run_turn(text))

    async def send_and_wait(self, text: str) -> Result:
        """Helper for tests/scripts: run a turn and block until Result.
        Fully hook-aware (uses _run_turn logic)."""
        if self.state is AgentState.working:
            raise RuntimeError("session is already busy")

        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())

        # To return the final result, we add a transient observer.
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Result] = loop.create_future()

        def _capture(s, ev):
            if isinstance(ev, Result) and not fut.done():
                fut.set_result(ev)

        self.add_event_observer(_capture)
        try:
            await self._run_turn(text)
            return await fut
        finally:
            self._extra_event_observers.remove(_capture)

    async def deliver(self, msg: InboxMessage) -> Delivery:
        """Push an inbox message at this session. Wake if idle (the message
        lands into a turn now); buffer if mid-turn (queued — the turn-end
        hook chains a follow-up turn). Returns a receipt telling the sender
        which happened and, when queued, the message's 1-based position."""
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
            return Delivery(disposition="queued",
                            depth=len(self._inbox_buffer))
        await self._cancel_idle_watcher()
        # idle: drain everything we hold and wake
        batch = self._inbox_buffer
        self._inbox_buffer = []
        self._emit_dispatch(batch)
        text = _render_batch(batch)
        self._emit_state(AgentState.working, finished=False)
        self.metrics.start_turn(self._now())
        self._task = asyncio.create_task(self._run_turn(text))
        return Delivery(disposition="landed", depth=0)

    def cancel_pending(self, msg: InboxMessage) -> bool:
        """Remove a still-buffered message by object identity. Returns True
        if it was removed before dispatch, False if already dispatched or
        never queued here."""
        for i, m in enumerate(self._inbox_buffer):
            if m is msg:
                del self._inbox_buffer[i]
                return True
        return False

    async def _run_turn(self, text: str) -> None:
        """Unified path. Runs hooks, then harness, then observers."""
        self._unsolicited = False  # a real, prompted turn
        harness_name = getattr(self.agent, "harness", "unknown")
        handle = SessionHandle(
            handle=self.handle,
            agent_profile=self.agent_slug,
            harness=harness_name,
        )

        # 0. session_start hook — fires once, before pre_turn of the
        # first turn. Awaited so ordering across start → pre → harness
        # is deterministic; bounded by the runner's per-hook timeout.
        if not self._session_start_fired:
            self._session_start_fired = True
            await run_observer_hooks(
                SessionStartEvent(
                    session=handle,
                    project_root=self.project_root,
                ),
                _HOOK_REG["session_start"],
                state_dir=self.state_dir,
            )

        # 1. Pre-turn hooks
        ctx = PreTurnContext(
            session=handle,
            user_message=text,
            history=(),  # FIXME: fetch from metrics or session
            project_root=self.project_root,
        )
        composed = await run_pre_turn_hooks(
            ctx, _HOOK_REG["pre_turn"], state_dir=self.state_dir
        )

        if composed.block:
            # Short-circuit: fire a Result immediately
            res = Result(duration_ms=0, is_error=True)
            # Add a blocked_reason attribute for tests that expect it
            setattr(res, "blocked_reason", composed.block)
            # Fire an AssistantText so observers see WHY it was blocked
            fake_text = AssistantText(
                text=f"⚠ Turn blocked by hook: {composed.block}"
            )
            self._fire_event(fake_text)
            self._fire_event(res)
            self.metrics.commit(None, self._now())
            self._emit_state(AgentState.ready, finished=True)
            self._chain_if_pending()
            return

        # 2. Preparation
        to_send = text
        if composed.rewrite_user:
            to_send = composed.rewrite_user
        if composed.prepend_system:
            to_send = (
                f"<aegis_context>\n{composed.prepend_system}\n</aegis_context>\n\n"
                + to_send
            )

        # 3. Execution
        saw_result = False
        assistant_text_parts: list[str] = []
        try:
            if not self._started:
                await self._session.start()
                self._started = True
                self.metrics.begin_session(self._now())

            await self._session.send(to_send)
            async for ev in self._session.events():
                self._fire_event(ev)

                if isinstance(ev, AssistantText):
                    assistant_text_parts.append(ev.text)
                elif isinstance(ev, ToolUse):
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
        except Exception as e:
            log.exception("harness error in _run_turn")
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

        # 4. Post-turn hooks (fire-and-forget)
        post_ev = PostTurnEvent(
            session=ctx.session,
            user_message=text,
            assistant_message="".join(assistant_text_parts),
            project_root=self.project_root,
        )
        asyncio.create_task(
            run_observer_hooks(
                post_ev, _HOOK_REG["post_turn"], state_dir=self.state_dir
            )
        )

        self._chain_if_pending()

    def _fire_event(self, ev: Event) -> None:
        if self.on_event is not None:
            self.on_event(self, ev)
        for cb in self._extra_event_observers:
            cb(self, ev)

    def _chain_if_pending(self) -> None:
        if self._inbox_buffer:
            batch = self._inbox_buffer
            self._inbox_buffer = []
            self._emit_dispatch(batch)
            text = _render_batch(batch)
            self._emit_state(AgentState.working, finished=False)
            self.metrics.start_turn(self._now())
            self._task = asyncio.create_task(self._run_turn(text))
            return
        # No inbox messages. Some harnesses (Claude with a background
        # Monitor or sub-task) can emit events after Result without us
        # sending a prompt. Drain anything that arrived during this
        # turn synchronously so it doesn't spill into the next user
        # message, then arm an async watcher for events that arrive
        # later while truly idle.
        has_pending = getattr(
            self._session, "has_pending_event", lambda: False)
        if has_pending() and self._unsolicited_hold == 0:
            self._emit_state(AgentState.working, finished=False)
            self.metrics.start_turn(self._now())
            self._task = asyncio.create_task(self._drain_unsolicited_turn())
            return
        self._unsolicited = False  # settling idle — no turn in flight
        self._arm_idle_watcher()

    async def _drain_unsolicited_turn(self) -> None:
        """Consume one turn's worth of events the harness emitted
        without us sending a prompt (e.g. a Claude Monitor firing).
        Skips pre/post-turn hooks and ``session.send()`` — the harness
        is mid-stream, not waiting on input."""
        self._unsolicited = True
        saw_result = False
        try:
            async for ev in self._session.events():
                self._fire_event(ev)
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
        except Exception as e:
            log.exception("harness error in unsolicited drain")
            self.last_error = e
            if not saw_result:
                self.metrics.commit(None, self._now())
                self._emit_state(AgentState.error, finished=True)
            self._chain_if_pending()
            return
        if not saw_result:
            self.metrics.commit(None, self._now())
            self._emit_state(AgentState.error, finished=True)
        self._chain_if_pending()

    def _arm_idle_watcher(self) -> None:
        if not getattr(self._session, "supports_idle_events", False):
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_watcher_loop())

    async def _idle_watcher_loop(self) -> None:
        """Poll the harness for spontaneous events while the session is
        idle. On first arrival, promote it to an unsolicited turn —
        this exits the watcher; ``_chain_if_pending`` will re-arm it
        after the drain completes."""
        try:
            while True:
                if self.state is AgentState.working:
                    return  # something else took over
                has_pending = getattr(
                    self._session, "has_pending_event", lambda: False)
                if has_pending() and self._unsolicited_hold == 0:
                    self._emit_state(AgentState.working, finished=False)
                    self.metrics.start_turn(self._now())
                    self._task = asyncio.create_task(
                        self._drain_unsolicited_turn())
                    return
                await asyncio.sleep(self._idle_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("idle watcher error; standing down")

    async def _cancel_idle_watcher(self) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = None
            return
        self._idle_task.cancel()
        try:
            await self._idle_task
        except (asyncio.CancelledError, Exception):
            pass
        self._idle_task = None

    async def interrupt(self) -> None:
        await self._cancel_idle_watcher()
        if self.state is not AgentState.working:
            return
        # Stop consuming events first so the driver's interrupt drain (below)
        # owns the queue without a competing reader.
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Signal the harness subprocess to actually abort the turn — without
        # this, cancelling the read task alone leaves claude running to
        # completion (burning tokens, running tools) in the background.
        interrupt = getattr(self._session, "interrupt", None)
        if interrupt is not None:
            await interrupt()
        self.metrics.cancel_turn(self._now())
        self._unsolicited = False
        self._emit_state(AgentState.ready, finished=False)

    async def close(self, reason: str = "explicit") -> None:
        await self._cancel_idle_watcher()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._started:
            await self._session.close()
        harness_name = getattr(self.agent, "harness", "unknown")
        asyncio.create_task(
            run_observer_hooks(
                SessionEndEvent(
                    session=SessionHandle(
                        handle=self.handle, agent_profile=self.agent_slug,
                        harness=harness_name,
                    ),
                    project_root=self.project_root,
                    reason=reason,
                ),
                _HOOK_REG["session_end"],
                state_dir=self.state_dir,
            )
        )
        self._emit_close(reason)
