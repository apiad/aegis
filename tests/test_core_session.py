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
