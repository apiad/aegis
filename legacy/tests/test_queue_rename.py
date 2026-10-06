"""Regression: a rename must not detach a queue task from either end.

``QueueManager`` keys its in-flight workers by the handle it minted, and a
task names its producer by handle too (``enqueued_by``, ``callback_handle``).
``SessionManager.rename_handle`` carried the inbox, locks, token, monitors
and reminders, but never told the queue. So:

- A worker that renamed itself (the briefing tells every agent to) finished
  its turn and ``_finalize`` found no entry under the new name and returned.
  No callback, no close, and the ``max_parallel`` slot stayed taken until
  restart.
- A producer that renamed with a task in flight had its callback delivered
  to the old handle, where nobody would ever drain it.

Observed live on 2026-09-16: ``happy-hopper`` → ``analisis-selector-vs2``
and the Task 10b worker → ``fleet-repaint-fix`` both finished, both stayed
alive, and neither producer heard back.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent


class GatedHarness:
    """Speaks, waits for the test to open the gate, speaks again, ends the
    turn — so the rename can land *during* the worker's turn."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.closed = False

    async def start(self): ...
    async def send(self, t): ...

    async def close(self):
        self.closed = True

    async def events(self):
        yield AssistantText(text="Looking into it.", message_id="m1")
        await self.gate.wait()
        yield AssistantText(text="Fixed it.", message_id="m2")
        yield Result(duration_ms=1, is_error=False, usage=None)
        await asyncio.Event().wait()


async def _settle(n: int = 80) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def _rig(cap: int = 1):
    harnesses: dict[str, GatedHarness] = {}

    def make_session(profile, url, handle):
        return harnesses.setdefault(handle, GatedHarness())

    inbox = InboxRouter()
    sm = SessionManager(
        {"default": object()}, "default",
        make_session=make_session, mcp=None, inbox=inbox,
        roots=AegisRoots.for_project(Path.cwd()),
    )
    names = iter(["worker-one", "worker-two"])
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="default", max_parallel=cap)},
        sm, inbox, handle_factory=lambda used: next(names),
    )
    sm.attach_queue_manager(qm)
    return sm, qm, inbox, harnesses


@pytest.mark.asyncio
async def test_a_worker_renamed_mid_turn_still_reports_back_and_frees_its_slot():
    sm, qm, inbox, harnesses = _rig(cap=1)
    tid, _ = qm.enqueue("impl", "fix it", enqueued_by=sender_agent("producer"),
                        callback=True, callback_handle="producer")
    second, _ = qm.enqueue("impl", "next", enqueued_by=sender_agent("producer"))
    await _settle()

    assert (await sm.rename_handle("worker-one", "fix-the-thing"))["ok"]
    harnesses["worker-one"].gate.set()
    await _settle()

    st = qm.status(tid)
    assert st["status"] == "completed", "the task stayed dispatched forever"
    assert st["result"] == "Fixed it."
    assert qm._all[tid].worker_handle == "fix-the-thing"
    assert [m.body for m in inbox.pending("producer")] == ["Fixed it."], (
        "the producer never got its callback")
    assert harnesses["worker-one"].closed, "the finished worker was left alive"
    assert sm.get("fix-the-thing") is None
    assert qm.status(second)["status"] == "dispatched", (
        "the max_parallel slot was never released")


@pytest.mark.asyncio
async def test_a_producer_renamed_with_a_task_in_flight_gets_the_callback():
    sm, qm, inbox, harnesses = _rig(cap=1)
    tid, _ = qm.enqueue("impl", "fix it", enqueued_by=sender_agent("producer"),
                        callback=True, callback_handle="producer")
    waiting, _ = qm.enqueue("impl", "next", enqueued_by=sender_agent("producer"),
                            callback=True, callback_handle="producer")
    await _settle()

    qm.rename("producer", "renamed-producer")

    # Both the running and the pending task follow the producer — the fleet
    # snapshot reads `callback_handle` to show who is waiting on a callback.
    for t in (qm._all[tid], qm._all[waiting]):
        assert t.enqueued_by == sender_agent("renamed-producer")
        assert t.callback_handle == "renamed-producer"
    assert qm._pending["impl"][0].callback_handle == "renamed-producer"
    assert qm._workers["worker-one"][0].enqueued_by == sender_agent("renamed-producer")

    harnesses["worker-one"].gate.set()
    await _settle()

    assert not inbox.pending("producer")
    assert [m.body for m in inbox.pending("renamed-producer")] == ["Fixed it."]


@pytest.mark.asyncio
async def test_a_rename_leaves_other_producers_tasks_alone():
    """``callback_handle`` on a task from a remote names a session on the
    *other* host; only a local ``agent:<old>`` producer is ours to move."""
    sm, qm, inbox, harnesses = _rig(cap=1)
    tid, _ = qm.enqueue("impl", "fix it", enqueued_by="remote:vps",
                        callback=True, callback_handle="producer")
    await _settle()

    qm.rename("producer", "renamed-producer")

    assert qm._all[tid].enqueued_by == "remote:vps"
    assert qm._all[tid].callback_handle == "producer"
