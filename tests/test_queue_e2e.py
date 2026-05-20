"""End-to-end hermetic proof of the VS1 task-queue loop + VS2 restart replay.

Walks one delegation from producer's enqueue, through QueueManager dispatch
(via a stub session-manager scripting FakeHarness events), through the
worker's _run_turn → on_state observer → _finalize → InboxRouter.deliver,
all the way to the producer's AgentSession waking on inbox arrival and
sending the substrate-rendered batch as a user turn. No disk, no real
claude — proves the wiring composes correctly.

VS2 additions: a hand-written-log replay test (deterministic, isolates
the restart-replay machinery) and a round-trip test that crashes a
hanging worker mid-flight then restarts a fresh QueueManager against
the same state_dir.
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


HANG = object()  # sentinel: harness's event stream blocks forever


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


class HangingHarness:
    """Used by the restart-replay test — worker never finishes, so the
    task stays in `dispatched` state and the JSONL log captures only
    enqueued + dispatched (no completed/failed) before we 'crash'."""

    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        await asyncio.Event().wait()
        if False:  # pragma: no cover — unreachable, keeps it a generator
            yield


class StubSM:
    def __init__(self):
        self._sessions: list[AgentSession] = []
        self._scripts: dict[str, list] = {}
        self.closed: list[str] = []

    def script(self, handle, events):
        self._scripts[handle] = events

    def spawn(self, slug, *, opening_prompt=None, handle=None):
        script = self._scripts.get(
            handle,
            [AssistantText(text="ok"),
             Result(duration_ms=1, is_error=False, usage=None)],
        )
        harness = HangingHarness() if script is HANG else FakeHarness(script)
        s = AgentSession(harness, None, slug, handle)
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


async def test_restart_replays_handwritten_log_into_failed_interrupted(tmp_path):
    """Deterministic isolation of the replay machinery: write a queue log
    by hand (enqueued + dispatched, no completion) and assert start()
    marks it failed:interrupted with a callback in the producer's inbox."""
    from aegis.queue.jsonl import append_record, read_records

    qfile = tmp_path / "queues" / "impl.jsonl"
    append_record(qfile, {
        "event": "enqueued", "task_id": "01TID1", "queue": "impl",
        "payload": "go", "enqueued_by": sender_agent("lucid-knuth"),
        "enqueued_at": "2026-05-20T07:14:00Z", "callback": True})
    append_record(qfile, {
        "event": "dispatched", "task_id": "01TID1",
        "worker_handle": "vivid-laplace"})

    inbox = InboxRouter(state_dir=tmp_path)
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="claude-impl",
                       max_parallel=1)},
        StubSM(), inbox, state_dir=tmp_path)
    await qm.start()

    st = qm.status("01TID1")
    assert st["status"] == "failed"
    assert "interrupted" in (st["error"] or "")

    # Failure callback persisted to the producer's inbox file.
    inbox_log = read_records(tmp_path / "inboxes" / "lucid-knuth.jsonl")
    assert any(
        r.get("task_id") == "01TID1" and r.get("status") == "error"
        for r in inbox_log)

    # The replay also appended a "failed" record to the queue log so
    # subsequent restarts treat it as completed (idempotent).
    qlog = read_records(qfile)
    assert qlog[-1]["event"] == "failed"
    assert qlog[-1]["task_id"] == "01TID1"


async def test_restart_round_trip_crashes_in_flight_and_recovers(tmp_path):
    """Full round-trip: enqueue → dispatch (worker hangs) → 'crash' →
    fresh QueueManager.start() against the same state_dir reads the log
    and produces failed:interrupted + callback in the inbox file."""
    from aegis.queue.jsonl import read_records

    # --- Round 1: enqueue + dispatch, then walk away ---
    inbox1 = InboxRouter(state_dir=tmp_path)
    sm1 = StubSM()
    sm1.script("w1", HANG)
    qm1 = QueueManager(
        {"impl": Queue(name="impl", agent_profile="claude-impl",
                       max_parallel=1)},
        sm1, inbox1, state_dir=tmp_path,
        handle_factory=lambda used: "w1")
    tid, _ = qm1.enqueue("impl", "go",
                         enqueued_by=sender_agent("lucid-knuth"),
                         callback=True)
    await asyncio.sleep(0.02)   # dispatch fires; worker w1 hangs forever

    # Sanity: at this point the log has enqueued + dispatched, no completion.
    qlog = read_records(tmp_path / "queues" / "impl.jsonl")
    events = [r["event"] for r in qlog]
    assert events == ["enqueued", "dispatched"]

    # --- Round 2: fresh process — replay from the persisted log ---
    inbox2 = InboxRouter(state_dir=tmp_path)
    qm2 = QueueManager(
        {"impl": Queue(name="impl", agent_profile="claude-impl",
                       max_parallel=1)},
        StubSM(), inbox2, state_dir=tmp_path)
    await qm2.start()

    st = qm2.status(tid)
    assert st["status"] == "failed"
    assert "interrupted" in (st["error"] or "")

    inbox_log = read_records(tmp_path / "inboxes" / "lucid-knuth.jsonl")
    assert any(r.get("task_id") == tid and r.get("status") == "error"
               for r in inbox_log)
