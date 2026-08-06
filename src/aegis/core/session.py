from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from aegis.drivers.base import HarnessSession
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, Event, Result,
    ThinkingTokens, ToolResult, ToolUse,
)
from aegis.plan import PlanSnapshot, PlanState, PlanTracker
from aegis.hooks import (
    PostTurnEvent, PreTurnContext, PreTurnResult, SessionEndEvent,
    SessionHandle, SessionStartEvent, Turn,
)
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG
from aegis.hooks.runner import run_observer_hooks, run_pre_turn_hooks
from aegis.core.loop import DEFAULT_MAX_ITERATIONS, LoopState
from aegis.queue.schema import (
    Delivery, InboxMessage, now_iso, render_inbox_header, sender_loop,
)
from aegis.tui.metrics import SessionMetrics, context_window_for
from aegis.tui.state import AgentState

log = logging.getLogger("aegis.core.session")

# Seeing any of these in a replayed log means the session was mid-turn at
# that stamp. Same set ``session_log`` uses to decide whether a log ends
# interrupted — kept deliberately in step with it.
_REPLAY_TURN_EVENTS = (AssistantText, AssistantThinking, ToolUse)

EventCb = Callable[["AgentSession", Event], None]
StateCb = Callable[["AgentSession", AgentState, bool], None]
InboxCb = Callable[["AgentSession", InboxMessage], None]
DispatchCb = Callable[["AgentSession", list[InboxMessage]], None]
CloseCb = Callable[["AgentSession", str], None]
LoopCb = Callable[["AgentSession", "LoopState | None", str], None]


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
                 project_root: Path | None = None,
                 log_id: str | None = None,
                 place=None) -> None:
        self._session = session
        self.agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        # Which machine and working tree this session's harness runs in.
        # Local import: core.session is imported early and aegis.hosts
        # pulls in aegis.mcp transitively.
        from aegis.hosts.models import Place
        self.place = place or Place("local", str(project_root or Path.cwd()))
        # Identity of this session's transcript on disk. Minted once and
        # never changed — unlike `handle`, which is recycled out of a finite
        # pool and can be renamed mid-session. Resume passes the stored id.
        from aegis.state.session_log import new_log_id
        self.log_id = log_id or new_log_id(handle)
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
        # Plan state is session state, not view state: the TUI strip, the
        # web client, and the MCP coordination plane are all readers.
        self.plan = PlanTracker()
        # Subagent plans, keyed by the dispatching Task tool_use id. Kept
        # apart from the top-level plan because a subagent's short list
        # would otherwise overwrite its parent's — the strip would read
        # 0/1 while the real plan is 5/8.
        self.subplans: dict[str, PlanTracker] = {}
        self._started = False
        self._task: asyncio.Task | None = None
        self._inbox = inbox                       # InboxRouter | None
        self._inbox_buffer: list[InboxMessage] = []
        # Self-left turn-end reminders. Drained by _chain_if_pending as the
        # LOWEST-priority tier — strictly after buffered inbox messages and
        # after any unsolicited harness-event drain. This is the "last thing"
        # the session does before it would otherwise settle idle.
        self._reminders: list[InboxMessage] = []
        # The operator's `/loop` instruction, or None. Drained by
        # _chain_if_pending as the LOWEST tier of all — below reminders —
        # and re-armed rather than consumed. See aegis/core/loop.py.
        self._loop: LoopState | None = None
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
        # Fired on arm / fire / stop so a frontend can render the loop chip
        # and announce termination. (session, state_or_None, reason).
        self.on_loop: LoopCb | None = None
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

    def adopt(self, session: HarnessSession) -> None:
        """Replace the underlying harness session in place.

        Everything aegis owns survives: handle, log_id, inbox binding,
        metrics, observers, transcript. Only the process at the bottom is
        new. Used by reconnect after a dropped remote link — which is why
        the tab keeps its history instead of being respawned.
        """
        self._session = session
        self.state = AgentState.ready

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

    def capture_next_reply(self, *, sink: list | None = None):
        """Arm a one-shot capture of this session's next complete reply.

        Returns a future resolving to the assistant text of the next turn,
        accumulated across streamed chunks and terminated by ``Result``.
        Subagent narration (anything carrying a ``parent_tool_use_id``) is
        left out: a peer that runs a ``Task`` must not fold its subagent's
        commentary into the answer the operator reads.

        **Arming is synchronous** and that matters — delivering to an idle
        session starts its turn inside ``deliver``, so a caller that armed
        from a task instead would race the very turn it wants to capture.

        ``sink``, when given, receives the terminating ``Result`` — the
        caller's way to learn what the turn cost without a second observer.
        """
        import asyncio

        from aegis.events import AssistantText, Result

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        chunks: list[str] = []

        def _obs(_sess, ev) -> None:
            if fut.done():
                return
            if isinstance(ev, AssistantText):
                if getattr(ev, "parent_tool_use_id", None) is None:
                    chunks.append(ev.text)
            elif isinstance(ev, Result):
                if sink is not None:
                    sink.append(ev)
                fut.set_result("".join(chunks))
                # Detach here rather than in a finally: the waiter may be
                # cancelled or timed out, and a leaked observer on a
                # long-lived session accumulates for the rest of its life.
                self.remove_event_observer(_obs)

        self.add_event_observer(_obs)
        fut.add_done_callback(
            lambda _f: self.remove_event_observer(_obs))
        return fut

    def remove_event_observer(self, cb: EventCb) -> None:
        """Unsubscribe an event callback. Idempotent — a one-shot observer
        that already detached itself must not raise on a second removal."""
        try:
            self._extra_event_observers.remove(cb)
        except ValueError:
            pass

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
        # Working time accrues only mid-turn, so every tracker follows the
        # session's turn state — including the subagents', which are also
        # only doing work while this session's turn is live.
        self._trackers_working(state == AgentState.working, self._now())
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

    def add_reminder(self, msg: InboxMessage) -> None:
        """Buffer a self-left note to be delivered as this session's own
        LAST turn. It fires at the next turn boundary, strictly behind any
        buffered inbox messages and any unsolicited harness-event drain
        (see ``_chain_if_pending``).

        A reminder is normally left mid-turn (the agent calls ``aegis_remind``
        during a turn), so the turn-boundary chain picks it up when that turn
        ends. If the session happens to be idle when this lands, nothing else
        would poke it — so promote the chain now.
        """
        self._reminders.append(msg)
        if self.state is not AgentState.working:
            self._chain_if_pending()

    def arm_loop(self, text: str,
                 max_iterations: int = DEFAULT_MAX_ITERATIONS) -> None:
        """Arm (or replace) this session's looping instruction.

        Armed while idle nothing else would poke the session, so promote the
        chain now — ``/loop <text>`` should start working, not wait for the
        next unrelated turn.
        """
        self._loop = LoopState(text=text, max_iterations=max_iterations)
        self._emit_loop("armed")
        if self.state is not AgentState.working:
            self._chain_if_pending()

    def stop_loop(self, reason: str = "stopped") -> bool:
        """Reap the loop. Returns False when nothing was armed, so a double
        stop is harmless."""
        if self._loop is None:
            return False
        self._loop = None
        self._emit_loop(reason)
        return True

    def loop_status(self) -> dict | None:
        return self._loop.status() if self._loop is not None else None

    def _emit_loop(self, reason: str) -> None:
        if self.on_loop is not None:
            self.on_loop(self, self._loop, reason)

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
                elif isinstance(ev, ThinkingTokens):
                    self.metrics.observe_thinking(ev.delta)

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
            self.stop_loop("stopped after a harness error")
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
        # Fold before observers run, so a subscriber reading plan_state()
        # from its callback sees this event already applied.
        if isinstance(ev, AgentPlan):
            self._apply_plan(ev)
        if self.on_event is not None:
            self.on_event(self, ev)
        for cb in self._extra_event_observers:
            cb(self, ev)

    def _apply_plan(self, ev: AgentPlan, ts: float | None = None) -> None:
        """Route by parent: top-level plans to our own tracker, a
        subagent's to a tracker of its own.

        ``ts`` is explicit only on the replay path, where the persisted
        stamp is what reproduces the original working times."""
        now = self._now() if ts is None else ts
        if ev.parent_tool_use_id is None:
            self.plan.apply_plan(ev, ts=now)
            return
        tracker = self.subplans.get(ev.parent_tool_use_id)
        if tracker is None:
            tracker = self.subplans[ev.parent_tool_use_id] = PlanTracker()
            tracker.set_working(self.plan.working, ts=now)
        tracker.apply_plan(ev, ts=now)

    def _trackers_working(self, working: bool, ts: float) -> None:
        """The session's turn state drives every tracker — the subagents'
        too, since they are only doing work while this session's turn is
        live."""
        self.plan.set_working(working, ts=ts)
        for tracker in self.subplans.values():
            tracker.set_working(working, ts=ts)

    def rehydrate_plan(self, events, stamps) -> None:
        """Rebuild plan state from a replayed transcript.

        A restart used to wipe the task list off the strip and the dock: the
        tracker is per-process, and the only path back was a later TaskList
        *result*, which is reactive — nothing calls TaskList on resume, so
        the surface stayed blank until the agent happened to list its tasks.

        The tracker was built for exactly this. It never reads a clock and
        takes an explicit ts on every method, so feeding it the persisted
        events with their persisted stamps reproduces the original working
        times rather than restarting them at zero.

        Turn boundaries are recovered from the events themselves: any of the
        mid-turn event kinds means the session was working, and a Result
        closes the turn. The replay always ends idle — a log that stops
        mid-turn (an interrupted session) would otherwise leave a task
        accruing from its last stamp and report hours on the first paint.
        """
        last = 0.0
        for i, ev in enumerate(events):
            ts = (stamps[i] if i < len(stamps) else 0.0) or last
            last = ts
            if isinstance(ev, AgentPlan):
                # A plan is promoted from a TodoWrite/Task* tool call, so
                # it is itself proof the session was mid-turn — and the
                # originating ToolUse is not always in the log to say so.
                self._trackers_working(True, ts)
                self._apply_plan(ev, ts=ts)
            elif isinstance(ev, Result):
                self._trackers_working(False, ts)
            elif isinstance(ev, _REPLAY_TURN_EVENTS):
                self._trackers_working(True, ts)
        self._trackers_working(False, last)

    def plan_state(self) -> PlanState:
        return self.plan.snapshot(ts=self._now())

    def plan_roll_up(self) -> PlanSnapshot:
        return self.plan.roll_up(ts=self._now())

    def subplan_states(self) -> dict[str, PlanState]:
        now = self._now()
        return {k: t.snapshot(ts=now) for k, t in self.subplans.items()}

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
        # Lowest-priority tier: self-left turn-end reminders. Nothing is
        # buffered and no harness event is pending — the session would go
        # idle now. Instead, drain any reminders as one final turn. This is
        # "behind monitors and queues": their callbacks land in _inbox_buffer
        # (tier 1) and are consumed first; the reminder is the last thing.
        # Not gated on _unsolicited_hold — a background monitor may still be
        # watching; the reminder fires anyway and the monitor wakes us later.
        if self._reminders:
            batch = self._reminders
            self._reminders = []
            self._emit_dispatch(batch)
            text = _render_batch(batch)
            self._emit_state(AgentState.working, finished=False)
            self.metrics.start_turn(self._now())
            self._task = asyncio.create_task(self._run_turn(text))
            return
        # Lowest tier of all: the operator's looping instruction. Everything
        # else — inbox, unsolicited drain, reminders — has already had its
        # turn, so nothing is starved behind a loop.
        # Yields to an armed aegis monitor: the monitor is the authoritative
        # waker, and re-firing underneath it would spin. The counter does not
        # advance on a suppressed fire.
        if self._loop is not None and self._unsolicited_hold == 0:
            if self._loop.exhausted():
                self.stop_loop(
                    f"capped at {self._loop.max_iterations} iterations "
                    f"— the agent did not stop it")
            else:
                self._loop.iteration += 1
                msg = InboxMessage(
                    sender=sender_loop(self._loop.iteration,
                                       self._loop.max_iterations),
                    timestamp=now_iso(),
                    body=self._loop.render(self.handle))
                self._emit_dispatch([msg])
                self._emit_loop("fired")
                self._emit_state(AgentState.working, finished=False)
                self.metrics.start_turn(self._now())
                self._task = asyncio.create_task(
                    self._run_turn(_render_batch([msg])))
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
                elif isinstance(ev, ThinkingTokens):
                    self.metrics.observe_thinking(ev.delta)
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
            self.stop_loop("stopped after a harness error")
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

    async def interrupt(self, *, drain: bool = True) -> None:
        # Interrupt means stop. Without this the loop re-fires the instant the
        # interrupted turn ends and Esc can never escape it.
        self.stop_loop("interrupted")
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
        # The interrupted turn's normal exit runs _chain_if_pending; a
        # cancelled one never gets there, so anything buffered behind it
        # (monitor callbacks, queue results, chips) would strand until some
        # unrelated future poke. Dispatch it as its own turn — the inbox tier
        # only: reminders and the loop are deliberately NOT resumed by Esc.
        # ``drain=False`` is for callers that deliver their own message right
        # after us; their deliver() drains the whole buffer as one turn.
        if drain and self._inbox_buffer:
            batch = self._inbox_buffer
            self._inbox_buffer = []
            self._emit_dispatch(batch)
            self._emit_state(AgentState.working, finished=False)
            self.metrics.start_turn(self._now())
            self._task = asyncio.create_task(
                self._run_turn(_render_batch(batch)))

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
