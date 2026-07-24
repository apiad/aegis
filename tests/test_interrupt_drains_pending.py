"""Interrupting a turn must not strand what was queued behind it.

Esc (and the web's interrupt RPC) cut the live turn; anything buffered in
the session's inbox while it ran — monitor callbacks, queue results, chips
the user typed — has to start its own turn instead of waiting for some
unrelated future poke. The interrupt-then-deliver callers (send-with-
interrupt, handoff(interrupt=True), cancel) pass ``drain=False`` because
their own delivery drains the buffer one line later, as a single turn.
"""
from __future__ import annotations

import asyncio

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.queue import InboxMessage, sender_user
from aegis.tui.state import AgentState


class GatedHarness:
    """Turn 1 blocks on a gate; every later turn completes immediately."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.interrupted = False
        self._gate = asyncio.Event()
        self._turn = 0

    async def start(self) -> None:
        pass

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        pass

    async def interrupt(self) -> None:
        self.interrupted = True
        self._gate.set()

    async def events(self):
        self._turn += 1
        if self._turn == 1:
            await self._gate.wait()
        yield AssistantText(text="ok")
        yield Result(duration_ms=1, is_error=False, usage=None)


def _msg(body: str) -> InboxMessage:
    return InboxMessage(sender="monitor:1a2b", timestamp="2026-07-24T00:00:00Z",
                        body=body, task_id="01J42", status="ok")


def _session(h: GatedHarness) -> AgentSession:
    return AgentSession(h, agent=None, agent_slug="default", handle="h")


async def test_interrupt_drains_buffered_inbox_into_a_new_turn():
    h = GatedHarness()
    s = _session(h)
    await s.send("work")
    await asyncio.sleep(0)
    assert s.state is AgentState.working

    await s.deliver(_msg("build ✓ done (12s)"))
    assert len(s._inbox_buffer) == 1        # queued behind the live turn

    await s.interrupt()
    assert h.interrupted is True
    assert s._inbox_buffer == []
    assert s._task is not None
    await s._task
    assert len(h.sent) == 2
    assert "build ✓ done (12s)" in h.sent[1]


async def test_interrupt_with_nothing_pending_settles_idle():
    h = GatedHarness()
    s = _session(h)
    await s.send("work")
    await asyncio.sleep(0)

    await s.interrupt()
    await asyncio.sleep(0)
    assert s.state is AgentState.ready
    assert len(h.sent) == 1


async def test_interrupt_no_drain_leaves_the_buffer_for_the_caller():
    # send-with-interrupt: cut the turn, then deliver — the new message and
    # everything already queued go out together as ONE turn.
    h = GatedHarness()
    s = _session(h)
    await s.send("work")
    await asyncio.sleep(0)
    await s.deliver(_msg("earlier"))

    await s.interrupt(drain=False)
    assert len(s._inbox_buffer) == 1

    await s.deliver(InboxMessage(sender=sender_user(),
                                 timestamp="2026-07-24T00:00:01Z",
                                 body="do this instead"))
    assert s._task is not None
    await s._task
    assert len(h.sent) == 2
    assert "earlier" in h.sent[1]
    assert "do this instead" in h.sent[1]
