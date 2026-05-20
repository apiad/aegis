"""End-to-end hermetic proof of the VS1 task-queue loop.

Walks one delegation from producer's enqueue, through QueueManager dispatch
(via a stub session-manager scripting FakeHarness events), through the
worker's _run_turn → on_state observer → _finalize → InboxRouter.deliver,
all the way to the producer's AgentSession waking on inbox arrival and
sending the substrate-rendered batch as a user turn. No disk, no real
claude — proves the wiring composes correctly.
"""
from __future__ import annotations

import asyncio

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.queue import (
    InboxRouter,
    Queue,
    QueueManager,
    sender_agent,
)


class FakeHarness:
    def __init__(self, events):
        self._events = list(events)
        self.sent: list[str] = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


class StubSM:
    def __init__(self):
        self._sessions: list[AgentSession] = []
        self._scripts: dict[str, list] = {}
        self.closed: list[str] = []

    def script(self, handle, events):
        self._scripts[handle] = events

    def spawn(self, slug, *, opening_prompt=None, handle=None):
        evs = self._scripts.get(
            handle,
            [AssistantText(text="ok"),
             Result(duration_ms=1, is_error=False, usage=None)],
        )
        s = AgentSession(FakeHarness(evs), None, slug, handle)
        self._sessions.append(s)
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s

    async def close(self, handle):
        self.closed.append(handle)
        self._sessions = [s for s in self._sessions if s.handle != handle]


async def test_e2e_enqueue_to_callback_wakes_producer():
    inbox = InboxRouter()
    sm = StubSM()

    # Producer session, idle. Bound to the inbox so callbacks route to it.
    producer = AgentSession(
        FakeHarness(
            [AssistantText(text="thanks, will do"),
             Result(duration_ms=1, is_error=False, usage=None)],
        ),
        None, "producer", "lucid-knuth", inbox=inbox)
    inbox.bind_session("lucid-knuth", producer)

    # Worker script: "DONE" is the last assistant text → becomes result.
    sm.script("vivid-laplace",
              [AssistantText(text="DONE"),
               Result(duration_ms=1, is_error=False, usage=None)])

    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="claude-impl",
                       max_parallel=1)},
        sm, inbox,
        handle_factory=lambda used: "vivid-laplace")

    tid, pos = qm.enqueue("impl", "implement plan X",
                          enqueued_by=sender_agent("lucid-knuth"),
                          callback=True)
    assert pos == 1

    # Pump the loop until the task transitions to completed (or until we've
    # given it enough budget — 100ms is plenty for the in-memory pipeline).
    for _ in range(20):
        await asyncio.sleep(0.005)
        if qm.status(tid)["status"] == "completed":
            break

    st = qm.status(tid)
    assert st["status"] == "completed"
    assert "DONE" in (st["result"] or "")

    # Producer's harness saw exactly one user turn — the wake-on-idle —
    # carrying the substrate header and the worker's body.
    producer_sent = producer._session.sent
    assert len(producer_sent) == 1
    body = producer_sent[0]
    assert body.startswith("> from queue:impl · task#")
    assert tid in body
    assert "DONE" in body

    # Worker was closed by the substrate after finalize.
    assert "vivid-laplace" in sm.closed
