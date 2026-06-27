from __future__ import annotations

import asyncio

from aegis.core.session import AgentSession, _render_batch
from aegis.events import AssistantText, Result
from aegis.queue import InboxMessage, sender_user
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


def _user(body, ts="2026-05-20T07:14:00Z"):
    return InboxMessage(sender=sender_user(), timestamp=ts, body=body)


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


async def test_deliver_returns_landed_when_idle():
    evs = [[Result(duration_ms=1, is_error=False, usage=None)]]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    receipt = await s.deliver(_msg("hello"))
    assert receipt.disposition == "landed" and receipt.depth == 0
    await s._task


async def test_deliver_returns_queued_with_depth_when_working():
    evs = [
        [AssistantText(text="working"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [Result(duration_ms=1, is_error=False, usage=None)],
    ]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    await s.send("work")
    r1 = await s.deliver(_msg("a"))
    r2 = await s.deliver(_msg("b"))
    assert (r1.disposition, r1.depth) == ("queued", 1)
    assert (r2.disposition, r2.depth) == ("queued", 2)
    await s._task
    await s._task


async def test_on_dispatch_fires_with_batch_on_idle_and_chain():
    evs = [
        [AssistantText(text="working"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [Result(duration_ms=1, is_error=False, usage=None)],
    ]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    batches: list[list[InboxMessage]] = []
    s.add_dispatch_observer(lambda _s, batch: batches.append(batch))

    # idle deliver → dispatched immediately as its own batch
    await s.deliver(_user("first"))
    await s._task
    assert [m.body for m in batches[0]] == ["first"]

    # mid-turn deliver of two → one chained batch at turn end
    await s.send("work")
    await s.deliver(_user("a"))
    await s.deliver(_user("b"))
    await s._task          # send-turn
    await s._task          # chained dispatch
    assert [m.body for m in batches[-1]] == ["a", "b"]


async def test_on_dispatch_does_not_fire_for_send():
    evs = [[Result(duration_ms=1, is_error=False, usage=None)]]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    fired: list = []
    s.add_dispatch_observer(lambda _s, batch: fired.append(batch))
    await s.send("plain")
    await s._task
    assert fired == []


async def test_cancel_pending_removes_by_identity():
    evs = [
        [AssistantText(text="working"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [Result(duration_ms=1, is_error=False, usage=None)],
    ]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    await s.send("work")
    a, b = _user("a"), _user("b")
    await s.deliver(a)
    await s.deliver(b)
    assert s.cancel_pending(a) is True
    await s._task          # send-turn
    await s._task          # chained dispatch of [b] only
    # only b survived to reach the harness
    assert "b" in s._session.sent[1] and "a" not in s._session.sent[1]


async def test_cannot_cancel_dispatched_message():
    evs = [[Result(duration_ms=1, is_error=False, usage=None)]]
    s = AgentSession(FakeHarness(evs), agent=None, agent_slug="d", handle="h")
    m = _user("gone")
    await s.deliver(m)     # idle → dispatched immediately
    await s._task
    assert s.cancel_pending(m) is False


def test_render_batch_user_is_headerless_handoff_keeps_header():
    user = InboxMessage(sender=sender_user(),
                        timestamp="2026-05-20T07:14:00Z", body="typed this")
    handoff = InboxMessage(sender="agent:wry-hopper",
                           timestamp="2026-05-20T07:14:00Z", body="peer work")
    out = _render_batch([user, handoff])
    assert out.startswith("typed this")
    assert "> from agent:wry-hopper" in out
    assert "> from user" not in out


async def test_deliver_fires_on_inbox_observer():
    """Frontends need a synchronous hook to render incoming inbox
    messages as they arrive, regardless of whether the session is idle
    (immediate dispatch) or mid-turn (buffered + chained)."""
    seen: list[InboxMessage] = []

    evs = [
        [AssistantText(text="ok"),
         Result(duration_ms=1, is_error=False, usage=None)],
        [AssistantText(text="reply"),
         Result(duration_ms=1, is_error=False, usage=None)],
    ]
    h = FakeHarness(evs)
    s = AgentSession(h, agent=None, agent_slug="default", handle="h")
    s.on_inbox = lambda _s, msg: seen.append(msg)

    # idle delivery — fires immediately
    await s.deliver(_msg("hello"))
    await s._task
    assert len(seen) == 1
    assert seen[0].body == "hello"

    # mid-turn delivery — also fires immediately, even though the
    # message gets buffered for the chained turn
    await s.send("work")
    await s.deliver(_msg("interrupt"))
    assert len(seen) == 2
    assert seen[1].body == "interrupt"
    # cleanup pending tasks
    await s._task
    if s._task is not None and not s._task.done():
        await s._task
