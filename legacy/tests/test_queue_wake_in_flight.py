"""A wake that lands during a worker's last turn must not be closed with it.

Issue #17. A queue worker armed a monitor, and the monitor tripped while the
worker was still writing its "I'll wait" message. The wake went into the
session's own inbox buffer, because a working session buffers rather than
starting a turn. `_run_turn` emits the finished state the moment the harness
sends its Result, but only chains the buffered wake after it has built the
turn's digest. The queue's finalizer ran in between: the monitor was no
longer live, the session read `ready`, and the guard counted only messages the
router held for unbound handles. So the task completed on "I'll wait" and the
session was closed with the wake still inside it.

Driven through a real `AgentSession` because the defect is the order of
events inside `_run_turn`, which a stub session cannot have.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result, ToolResult, ToolUse
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent
from aegis.queue.schema import InboxMessage, now_iso, sender_monitor
from tests.test_queue_e2e import StubSM

WORKER = "semilla-cabecera"
WAKE = "API SIGERE responde — done (3s)"


class WakeMidTurnHarness:
    """Turn 1 delivers a monitor wake before its Result; turn 2 answers it."""

    def __init__(self, inbox: InboxRouter):
        self._inbox = inbox
        self.sent: list[str] = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        if len(self.sent) == 1:
            # The incident's worker had been writing code that turn. A write
            # is what makes the turn digest diff a repo off the event loop,
            # and that await is the window the finalizer ran in.
            yield ToolUse(
                name="Write",
                summary="api.py",
                raw_input={"file_path": str(Path.cwd() / "api.py")},
                kind="edit",
                tool_call_id="w1",
            )
            yield ToolResult(text="ok", is_error=False, tool_call_id="w1")
            yield AssistantText(text="Waiting; the monitor wakes me.")
            await self._inbox.deliver(
                WORKER,
                InboxMessage(
                    sender=sender_monitor("Q3FQ"),
                    timestamp=now_iso(),
                    body=WAKE,
                    task_id="01M3MVNNDCTGAQAQ6D0FK1Q3FQ",
                    status="ok",
                ),
            )
        else:
            yield AssistantText(text="FINISHED AFTER WAKE")
        yield Result(duration_ms=1, is_error=False, usage=None)


class BoundSM(StubSM):
    """StubSM, but its workers are bound to the inbox, as the real
    SessionManager binds every session it spawns."""

    def __init__(self, inbox: InboxRouter):
        super().__init__()
        self.inbox_router = inbox
        self.harness = WakeMidTurnHarness(inbox)

    async def close(self, handle, *a, **kw):
        # How many turns the worker had been sent when the queue closed it.
        self.turns_at_close = len(self.harness.sent)
        return await super().close(handle, *a, **kw)

    def spawn(self, slug, *, opening_prompt=None, handle=None, **kw):
        s = AgentSession(self.harness, None, slug, handle, project_root=Path.cwd())
        self._sessions.append(s)
        self._ever[handle] = s
        self.inbox_router.bind_session(handle, s)
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s


async def _run():
    inbox = InboxRouter()
    sm = BoundSM(inbox)
    qm = QueueManager(
        {"q": Queue(name="q", agent_profile="impl", max_parallel=1)},
        sm,
        inbox,
        handle_factory=lambda used: WORKER,
    )
    tid, _ = qm.enqueue(
        "q", "start the API and wait for it", enqueued_by=sender_agent("p")
    )
    for _ in range(100):
        await asyncio.sleep(0.005)
        if qm.status(tid)["status"] == "completed":
            break
    return qm, sm, tid


async def test_the_wake_reaches_a_live_worker():
    _, sm, _ = await _run()
    assert len(sm.harness.sent) == 2
    assert WAKE in sm.harness.sent[1]
    assert sm.turns_at_close == 2, (
        "the queue closed the worker before its buffered monitor wake ran"
    )


async def test_the_task_result_is_the_answer_not_the_promise():
    qm, _, tid = await _run()
    st = qm.status(tid)
    assert st["status"] == "completed"
    assert "FINISHED AFTER WAKE" in (st["result"] or "")
