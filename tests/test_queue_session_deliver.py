from __future__ import annotations

import asyncio

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.queue import InboxMessage
from aegis.tui.state import AgentState


class FakeHarness:
    def __init__(self, events_per_turn):
        # events_per_turn: list of lists; one inner list per turn.
        self._turns = list(events_per_turn)
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
        evs = self._turns.pop(0) if self._turns else []
        for e in evs:
            await asyncio.sleep(0)
            yield e


def _msg(body, ts="2026-05-20T07:14:00Z"):
    return InboxMessage(sender="queue:impl", timestamp=ts, body=body,
                        task_id="01J42", status="ok")


async def test_idle_delivery_wakes_into_new_turn():
    evs = [
        [AssistantText(text="ok"),
         Result(duration_ms=1, is_error=False, usage=None)],
    ]
    h = FakeHarness(evs)
    s = AgentSession(h, agent=None, agent_slug="default", handle="h")
    await s.deliver(_msg("hello"))
    # let the scheduled task run
    assert s._task is not None
    await s._task
    assert s.state is AgentState.ready
    assert len(h.sent) == 1
    body = h.sent[0]
    assert "> from queue:impl · task#01J42 · ok" in body
    assert "hello" in body


async def test_mid_turn_delivery_buffers_and_chains():
    # Turn 1: producer's current work. Turn 2: chain triggered by mid-turn delivery.
    evs = [
        [AssistantText(text="working"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [AssistantText(text="reply"),
         Result(duration_ms=1, is_error=False, usage=None)],
    ]
    h = FakeHarness(evs)
    s = AgentSession(h, agent=None, agent_slug="default", handle="h")
    await s.send("work")
    # state is now working; deliver mid-turn
    await s.deliver(_msg("interrupt"))
    # buffer, no second turn yet
    await s._task
    # the chain task should be set and will run a follow-up turn
    chain = s._task
    assert chain is not None
    await chain
    assert len(h.sent) == 2
    assert h.sent[0] == "work"
    assert "interrupt" in h.sent[1]


async def test_extra_observers_fire_alongside_primary():
    """Primary on_event / on_state slots stay claimed (e.g. by the TUI
    pane's renderer); add_*_observer subscriptions fire in addition."""
    evs = [
        [AssistantText(text="hi"),
         Result(duration_ms=1, is_error=False, usage=None)],
    ]
    h = FakeHarness(evs)
    s = AgentSession(h, agent=None, agent_slug="default", handle="h")

    primary_events: list = []
    extra_events: list = []
    primary_states: list = []
    extra_states: list = []
    s.on_event = lambda _s, ev: primary_events.append(ev)
    s.on_state = lambda _s, st, finished: primary_states.append((st, finished))
    s.add_event_observer(lambda _s, ev: extra_events.append(ev))
    s.add_state_observer(
        lambda _s, st, finished: extra_states.append((st, finished)))

    await s.send("go")
    await s._task
    # Both observers saw the same stream of events and the same state
    # transitions. Ordering: primary then extras (deterministic).
    assert len(primary_events) == 2 and len(extra_events) == 2
    assert primary_states == extra_states
    assert primary_states[-1] == (AgentState.ready, True)


async def test_multiple_arrivals_batch_into_one_chain_turn():
    evs = [
        [AssistantText(text="working"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [AssistantText(text="reply"),
         Result(duration_ms=1, is_error=False, usage=None)],
    ]
    h = FakeHarness(evs)
    s = AgentSession(h, agent=None, agent_slug="default", handle="h")
    await s.send("work")
    await s.deliver(_msg("a"))
    await s.deliver(_msg("b"))
    await s._task          # first turn
    await s._task          # chain
    assert len(h.sent) == 2
    body = h.sent[1]
    # both bodies present, each with own header
    assert body.count("> from queue:impl") == 2
    assert "a" in body and "b" in body
