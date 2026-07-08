from __future__ import annotations

import asyncio

import pytest

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.tui.state import AgentState


class FakeSession:
    def __init__(self, events):
        self._events = list(events)
        self.started = False
        self.closed = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


@pytest.mark.asyncio
async def test_lazy_start_and_observers_and_commit():
    evs = [AssistantText(text="hi"),
           Result(duration_ms=10, is_error=False, usage=None)]
    s = AgentSession(FakeSession(evs), agent=None, agent_slug="default",
                     handle="h1")
    seen: list[str] = []
    states: list[tuple[AgentState, bool]] = []
    s.on_event = lambda se, e: seen.append(type(e).__name__)
    s.on_state = lambda se, st, fin: states.append((st, fin))
    await s.send("do x")
    await s._task
    assert s._session.started and s._session.sent == ["do x"]
    assert seen == ["AssistantText", "Result"]
    assert states == [(AgentState.working, False),
                      (AgentState.ready, True)]


@pytest.mark.asyncio
async def test_exit_without_result_is_error():
    s = AgentSession(FakeSession([AssistantText(text="partial")]),
                     None, "default", "h1")
    st: list[tuple[AgentState, bool]] = []
    s.on_state = lambda se, x, f: st.append((x, f))
    await s.send("x")
    await s._task
    assert st[-1] == (AgentState.error, True)


@pytest.mark.asyncio
async def test_interrupt_cancels_and_resets():
    class Hang(FakeSession):
        async def events(self):
            while True:
                await asyncio.sleep(0.01)
                yield  # never reached

    s = AgentSession(Hang([]), None, "default", "h1")
    st: list[tuple[AgentState, bool]] = []
    s.on_state = lambda se, x, f: st.append((x, f))
    await s.send("x")
    await asyncio.sleep(0.02)
    await s.interrupt()
    assert s.state is AgentState.ready
    assert st[-1] == (AgentState.ready, False)


@pytest.mark.asyncio
async def test_interrupt_signals_the_harness():
    """interrupt() must tell the driver session to abort — cancelling the
    local read task alone leaves the real subprocess running."""
    class Hang(FakeSession):
        def __init__(self, events):
            super().__init__(events)
            self.interrupted = False

        async def events(self):
            while True:
                await asyncio.sleep(0.01)
                yield  # never reached

        async def interrupt(self):
            self.interrupted = True

    sess = Hang([])
    s = AgentSession(sess, None, "default", "h1")
    await s.send("x")
    await asyncio.sleep(0.02)
    await s.interrupt()
    assert sess.interrupted
    assert s.state is AgentState.ready


class WakeableFakeSession:
    """Models a harness like Claude that can spontaneously emit events
    after a Result (e.g. a Monitor background task firing). The pending
    queue is fed externally via :meth:`feed`; ``has_pending_event`` and
    ``supports_idle_events`` mirror the real ClaudeSession contract."""

    supports_idle_events = True

    def __init__(self, initial_events):
        self._pending: list = list(initial_events)
        self.started = False
        self.closed = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    def has_pending_event(self) -> bool:
        return bool(self._pending)

    def feed(self, *events):
        """Append spontaneous events the harness will emit."""
        self._pending.extend(events)

    async def events(self):
        # Yield one turn's worth: drain until (and including) a Result,
        # then return. If no Result is present, drain all and return.
        while self._pending:
            await asyncio.sleep(0)
            ev = self._pending.pop(0)
            yield ev
            if isinstance(ev, Result):
                return


@pytest.mark.asyncio
async def test_unsolicited_events_drained_at_turn_end():
    """Events that arrive in the harness queue during the turn (after
    the Result that closed events()) must be drained as their own
    follow-up turn — not left to spill into the next user message."""
    s = AgentSession(
        WakeableFakeSession([
            AssistantText(text="first"),
            Result(duration_ms=1, is_error=False, usage=None),
        ]),
        agent=None, agent_slug="default", handle="h1")
    seen: list[str] = []
    s.on_event = lambda se, e: seen.append(
        getattr(e, "text", type(e).__name__))
    # Before the turn ends, the "harness" buffers a wake-up event
    # (mimicking a Monitor that fired between the prior Result and
    # _chain_if_pending).
    s._session.feed(
        AssistantText(text="monitor-wake"),
        Result(duration_ms=1, is_error=False, usage=None),
    )
    await s.send("hello")
    await s._task
    # Give the chained drain turn a chance to complete.
    for _ in range(50):
        if "monitor-wake" in seen:
            break
        await asyncio.sleep(0.01)
    assert "monitor-wake" in seen, (
        "spontaneous post-Result events should drain as their own turn")
    assert s.state is AgentState.ready


@pytest.mark.asyncio
async def test_unsolicited_events_after_idle_trigger_turn():
    """When the harness emits an event AFTER the turn fully ended and
    the session is sitting idle, the idle watcher must promote it to
    an unsolicited turn instead of letting it queue silently."""
    s = AgentSession(
        WakeableFakeSession([
            AssistantText(text="first"),
            Result(duration_ms=1, is_error=False, usage=None),
        ]),
        agent=None, agent_slug="default", handle="h1")
    seen: list[str] = []
    s.on_event = lambda se, e: seen.append(
        getattr(e, "text", type(e).__name__))
    await s.send("hello")
    await s._task
    # Let the idle watcher arm.
    await asyncio.sleep(0.05)
    assert s.state is AgentState.ready
    # Now simulate Claude's Monitor firing while idle.
    s._session.feed(
        AssistantText(text="wake"),
        Result(duration_ms=1, is_error=False, usage=None),
    )
    # The watcher polls; give it time to notice + drain.
    for _ in range(50):
        if "wake" in seen:
            break
        await asyncio.sleep(0.05)
    assert "wake" in seen, (
        "idle watcher should detect post-idle harness events")
    assert s.state is AgentState.ready
    await s.close()
