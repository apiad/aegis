from __future__ import annotations

import asyncio

import pytest

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.queue import (
    InboxRouter,
    Queue,
    QueueManager,
    sender_agent,
    sender_queue,
)

HANG = object()  # sentinel: worker's event stream never completes


class FakeHarness:
    def __init__(self, events):
        self._events = list(events)
        self.started = self.closed = False
        self.sent: list[str] = []

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e


class HangingHarness:
    """Events generator blocks forever — proxies a worker that never returns
    a Result. Used to validate cap enforcement without a real subprocess."""

    def __init__(self):
        self.started = self.closed = False
        self.sent: list[str] = []

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        await asyncio.Event().wait()
        if False:  # pragma: no cover — unreachable, keeps it a generator
            yield


class StubSessionManager:
    """Records spawns and returns AgentSession instances we control."""

    def __init__(self):
        self.spawns: list[tuple] = []
        self._scripts: dict[str, object] = {}   # handle -> events or HANG
        self.closed: list[str] = []
        self._sessions: list[AgentSession] = []

    def script(self, handle: str, events_or_hang) -> None:
        self._scripts[handle] = events_or_hang

    def spawn(self, slug: str, *,
              opening_prompt: str | None = None,
              handle: str | None = None) -> AgentSession:
        assert handle is not None, "QueueManager must pass an explicit handle"
        script = self._scripts.get(
            handle,
            [AssistantText(text="DONE"),
             Result(duration_ms=1, is_error=False, usage=None)],
        )
        harness = HangingHarness() if script is HANG else FakeHarness(script)
        s = AgentSession(harness, agent=None, agent_slug=slug, handle=handle)
        self._sessions.append(s)
        self.spawns.append((slug, handle, opening_prompt, s))
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s

    async def close(self, handle: str) -> None:
        self.closed.append(handle)
        self._sessions = [s for s in self._sessions if s.handle != handle]

    async def interrupt(self, handle: str) -> None:
        self.interrupted = getattr(self, "interrupted", [])
        self.interrupted.append(handle)
        s = next((s for s in self._sessions if s.handle == handle), None)
        if s is not None:
            await s.interrupt()


def _q(name="impl", profile="claude-impl", cap=2,
       provider="", model=""):
    return Queue(name=name, agent_profile=profile, max_parallel=cap,
                 provider=provider, model=model)


async def test_enqueue_returns_id_and_position():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=0)}, sm, inbox)
    tid1, pos1 = qm.enqueue("impl", "a",
                            enqueued_by=sender_agent("p"), callback=False)
    tid2, pos2 = qm.enqueue("impl", "b",
                            enqueued_by=sender_agent("p"), callback=False)
    assert pos1 == 1 and pos2 == 2
    assert tid1 != tid2


async def test_unknown_queue_raises():
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox)
    with pytest.raises(KeyError):
        qm.enqueue("ghost", "x",
                   enqueued_by=sender_agent("p"), callback=False)


async def test_dispatch_respects_cap():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", HANG)  # worker 1 truly never finishes
    qm.enqueue("impl", "a",
               enqueued_by=sender_agent("p"), callback=False)
    qm.enqueue("impl", "b",
               enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.02)
    # cap=1 => only one spawn so far; the second task waits in FIFO
    assert [h for _slug, h, _p, _s in sm.spawns] == ["w1"]


async def test_callback_delivered_on_completion():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [AssistantText(text="all done"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    tid, _ = qm.enqueue("impl", "go",
                        enqueued_by=sender_agent("lucid-knuth"),
                        callback=True)
    # let the worker run and dispatch's observer fire
    await asyncio.sleep(0.05)
    pending = inbox.pending("lucid-knuth")
    assert len(pending) == 1
    msg = pending[0]
    assert msg.sender == sender_queue("impl")
    assert msg.task_id == tid
    assert msg.status == "ok"
    assert "all done" in msg.body
    # task status is completed
    st = qm.status(tid)
    assert st["status"] == "completed" and "all done" in st["result"]
    # worker was closed
    assert "w1" in sm.closed


async def test_failed_worker_delivers_error_callback():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [Result(duration_ms=1, is_error=True, usage=None)])
    tid, _ = qm.enqueue("impl", "go",
                        enqueued_by=sender_agent("lucid-knuth"),
                        callback=True)
    await asyncio.sleep(0.05)
    pending = inbox.pending("lucid-knuth")
    assert len(pending) == 1 and pending[0].status == "error"
    assert qm.status(tid)["status"] == "failed"


async def test_callback_false_skips_inbox_delivery():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [AssistantText(text="ok"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    qm.enqueue("impl", "go",
               enqueued_by=sender_agent("lucid-knuth"), callback=False)
    await asyncio.sleep(0.05)
    assert inbox.pending("lucid-knuth") == []


async def test_status_unknown_returns_none():
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox)
    assert qm.status("does-not-exist") is None


async def test_queue_manager_persists_lifecycle(tmp_path):
    from aegis.queue.jsonl import read_records

    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      state_dir=tmp_path,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [AssistantText(text="r"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    tid, _ = qm.enqueue("impl", "go",
                        enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.05)
    log = read_records(tmp_path / "queues" / "impl.jsonl")
    events = [r["event"] for r in log]
    assert events == ["enqueued", "dispatched", "completed"]
    assert log[0]["task_id"] == tid
    assert log[2]["result"] == "r"


async def test_cancel_pending_drops_from_fifo():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", HANG)  # first worker occupies the only slot
    qm.enqueue("impl", "a", enqueued_by=sender_agent("p"), callback=False)
    tid2, _ = qm.enqueue("impl", "b",
                         enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.02)
    # b is still pending (cap=1) — cancel it
    res = await qm.cancel(tid2)
    assert res["ok"] and res["status"] == "cancelled"
    assert qm.status(tid2)["status"] == "cancelled"
    # b never spawned
    assert [h for _s, h, _p, _ss in sm.spawns] == ["w1"]


async def test_cancel_pending_notifies_producer_when_callback():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", HANG)
    qm.enqueue("impl", "a", enqueued_by=sender_agent("p"), callback=False)
    tid2, _ = qm.enqueue("impl", "b",
                         enqueued_by=sender_agent("boss"), callback=True)
    await asyncio.sleep(0.02)
    await qm.cancel(tid2)
    pending = inbox.pending("boss")
    assert len(pending) == 1 and pending[0].status == "error"
    assert pending[0].task_id == tid2


async def test_cancel_inflight_interrupts_and_closes_worker():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", HANG)  # in-flight, never finishes on its own
    tid, _ = qm.enqueue("impl", "go",
                        enqueued_by=sender_agent("boss"), callback=True)
    await asyncio.sleep(0.02)
    assert qm.status(tid)["status"] == "dispatched"
    res = await qm.cancel(tid)
    assert res["ok"] and res["status"] == "cancelled"
    assert "w1" in getattr(sm, "interrupted", [])
    assert "w1" in sm.closed
    # the pre-empted finalizer must not overwrite the cancelled status
    await asyncio.sleep(0.02)
    assert qm.status(tid)["status"] == "cancelled"
    # producer got exactly one cancellation notice (no double callback)
    pending = inbox.pending("boss")
    assert len(pending) == 1 and pending[0].status == "error"


async def test_cancel_terminal_is_idempotent():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [AssistantText(text="done"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    tid, _ = qm.enqueue("impl", "go",
                        enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.05)
    assert qm.status(tid)["status"] == "completed"
    res = await qm.cancel(tid)
    assert res["ok"] and res["status"] == "completed"


async def test_cancel_unknown_task():
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox)
    res = await qm.cancel("nope")
    assert res["ok"] is False and "unknown" in res["error"]


async def test_cancel_inflight_frees_slot_for_next():
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", HANG)
    tid1, _ = qm.enqueue("impl", "a",
                         enqueued_by=sender_agent("p"), callback=False)
    qm.enqueue("impl", "b", enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.02)
    assert [h for _s, h, _p, _ss in sm.spawns] == ["w1"]
    await qm.cancel(tid1)          # free the only slot
    await asyncio.sleep(0.02)
    # b now dispatches into the freed slot (a second spawn happened)
    assert len(sm.spawns) == 2
    assert sm.spawns[1][2] == "b"   # opening_prompt of the second spawn


async def test_state_dir_none_writes_nothing(tmp_path):
    # Sanity: VS1 in-memory mode unchanged when state_dir is omitted.
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: "w1")
    sm.script("w1", [AssistantText(text="r"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    qm.enqueue("impl", "go",
               enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.05)
    assert not (tmp_path / "queues").exists()
