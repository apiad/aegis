# Aegis Queue Dashboard v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the queue-observability TUI surface from the spec: an always-on one-line strip in every conversation showing live queue depth + most recent worker, plus a `Ctrl+D` modal dashboard with `QUEUES / IN-FLIGHT / QUEUED / RECENT` bands and an inline detail panel.

**Architecture:** Add a push-based observer hook on `QueueManager` (no polling). A new `QueueDigest` service subscribes to manager + per-worker `AgentSession` event streams and exposes immutable `QueueView[]` / `TaskView[]` snapshots. The TUI strip widget binds to the digest. A Textual `ModalScreen` mounts four band widgets (left ⅔) and a detail panel (right ⅓) and binds the same digest plus live-tail ring buffers per task.

**Spec:** `docs/superpowers/specs/2026-05-21-aegis-queue-dashboard-design.html`.

**Tech stack:** Python 3.13, Textual 8.x, `dataclasses`, `pytest` (uv-run), `Textual.App.run_test` for TUI behavioural tests. No new dependencies.

**House conventions (read before starting):** `AGENTS.md`. Commit to `main` (authorized for aegis). Every task keeps `uv run pytest -q -m "not live"` green. Use `uv run pytest` (not bare `pytest`). One logical change per commit. Push after each task.

---

## File map

**Create:**
- `src/aegis/queue/events.py` — `QueueEvent` dataclasses + `Unsubscribe` alias.
- `src/aegis/queue/digest.py` — `QueueDigest` service.
- `src/aegis/tui/strip.py` — `QueueStrip` Textual widget.
- `src/aegis/tui/dashboard.py` — `QueueDashboard` ModalScreen + bands + `DetailPanel`.
- `tests/test_queue_events.py`
- `tests/test_queue_digest.py`
- `tests/test_tui_strip.py`
- `tests/test_tui_dashboard.py`

**Modify:**
- `src/aegis/queue/manager.py` — add `subscribe(callback) -> Unsubscribe`; emit `QueueEvent` after each JSONL write.
- `src/aegis/queue/__init__.py` — re-export new symbols.
- `src/aegis/tui/pane.py` — mount `QueueStrip` between transcript and `StatusBar`.
- `src/aegis/tui/app.py` — construct `QueueDigest`; register `Ctrl+D` binding; push `QueueDashboard`.

---

## Slice 1 — substrate event hook visible end-to-end

**End-of-slice acceptance:** Running `aegis` and triggering an enqueue makes a stub strip render in every conversation pane updating live with `queues: <name> ●N/M ○K …`. Dashboard not yet implemented.

### Task 1: `QueueEvent` types

**Files:**
- Create: `src/aegis/queue/events.py`
- Test: `tests/test_queue_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_events.py
from aegis.queue.events import (
    QueueEnqueued, QueueDispatched, QueueStarted, QueueCompleted,
    QueueEvent,
)


def test_event_construction_and_taxonomy():
    e1 = QueueEnqueued(task_id="t1", queue="tasks",
                       payload="hi", enqueued_by="agent:foo")
    e2 = QueueDispatched(task_id="t1", queue="tasks",
                         worker_handle="brisk-curie",
                         agent_slug="gemini-flash")
    e3 = QueueStarted(task_id="t1", queue="tasks")
    e4 = QueueCompleted(task_id="t1", queue="tasks",
                        outcome="completed",
                        result="ok", error=None,
                        completed_at="2026-05-21T12:00:00Z")
    for e in (e1, e2, e3, e4):
        assert isinstance(e, QueueEvent)
    assert e1.task_id == "t1"
    assert e2.worker_handle == "brisk-curie"
    assert e4.outcome == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queue_events.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.queue.events`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/queue/events.py
"""QueueEvent taxonomy + observer types.

Push-based observability surface for the queue substrate. Every
QueueManager state transition emits exactly one event after its
JSONL log entry is committed.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class QueueEnqueued:
    task_id: str
    queue: str
    payload: str
    enqueued_by: str


@dataclass(frozen=True)
class QueueDispatched:
    task_id: str
    queue: str
    worker_handle: str
    agent_slug: str


@dataclass(frozen=True)
class QueueStarted:
    task_id: str
    queue: str


@dataclass(frozen=True)
class QueueCompleted:
    task_id: str
    queue: str
    outcome: Literal["completed", "failed", "interrupted"]
    result: str | None
    error: str | None
    completed_at: str


QueueEvent = Union[
    QueueEnqueued, QueueDispatched, QueueStarted, QueueCompleted,
]

QueueObserver = Callable[[QueueEvent], None]
Unsubscribe = Callable[[], None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_queue_events.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/queue/events.py tests/test_queue_events.py
git commit -m "feat(queue): QueueEvent taxonomy for observer hook"
git push origin main
```

---

### Task 2: `QueueManager.subscribe` + emission

**Files:**
- Modify: `src/aegis/queue/manager.py`
- Test: `tests/test_queue_manager.py` (extend) — or create `tests/test_queue_observer.py` if extension is awkward.

The `QueueManager` writes JSONL records inside `_log` already. The observer fires immediately after `_log` returns. There is no `QueueStarted` event emitted by the substrate today (dispatched == started in v1) — emit it from `_try_dispatch` right after the dispatched record is logged, with the same `task_id`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queue_observer.py` (create file):

```python
# tests/test_queue_observer.py
import pytest

from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueStarted,
)
from aegis.queue.manager import QueueManager
from aegis.queue.schema import Queue
from tests.test_queue_manager import (  # reuse the stub fixtures
    FakeInbox, FakeSessionManager, _seed_handles,
)


def _make_manager(queues, sm, inbox):
    return QueueManager(
        {q.name: q for q in queues}, sm, inbox,
        handle_factory=_seed_handles(["worker-1", "worker-2"]))


@pytest.mark.asyncio
async def test_subscribe_receives_full_lifecycle():
    received: list = []
    sm, inbox = FakeSessionManager(), FakeInbox()
    qm = _make_manager(
        [Queue(name="tasks", agent_profile="claude", max_parallel=1)],
        sm, inbox)
    unsub = qm.subscribe(lambda ev: received.append(ev))

    task_id, _ = qm.enqueue(
        "tasks", "payload-1", enqueued_by="agent:caller", callback=False)
    # FakeSessionManager.spawn returns a session; drive it to ready
    # which triggers _finalize:
    await sm.last_session.complete("done")

    kinds = [type(e).__name__ for e in received]
    assert kinds == [
        "QueueEnqueued", "QueueDispatched",
        "QueueStarted", "QueueCompleted",
    ]
    enq, disp, started, comp = received
    assert isinstance(enq, QueueEnqueued)
    assert enq.task_id == task_id and enq.queue == "tasks"
    assert isinstance(disp, QueueDispatched)
    assert disp.worker_handle == "worker-1"
    assert isinstance(started, QueueStarted)
    assert isinstance(comp, QueueCompleted)
    assert comp.outcome == "completed" and comp.result == "done"

    unsub()
    qm.enqueue(
        "tasks", "payload-2", enqueued_by="agent:caller", callback=False)
    assert [type(e).__name__ for e in received] == [
        "QueueEnqueued", "QueueDispatched",
        "QueueStarted", "QueueCompleted",
    ]


@pytest.mark.asyncio
async def test_observer_exceptions_do_not_break_substrate():
    sm, inbox = FakeSessionManager(), FakeInbox()
    qm = _make_manager(
        [Queue(name="tasks", agent_profile="claude", max_parallel=1)],
        sm, inbox)

    def angry(ev):
        raise RuntimeError("nope")

    received: list = []
    qm.subscribe(angry)
    qm.subscribe(lambda ev: received.append(ev))

    qm.enqueue(
        "tasks", "p", enqueued_by="agent:caller", callback=False)
    # The second observer still got the enqueued event despite the
    # first observer raising.
    assert [type(e).__name__ for e in received] == [
        "QueueEnqueued", "QueueDispatched", "QueueStarted",
    ]
```

If `tests/test_queue_manager.py` does not already expose `FakeSessionManager`, `FakeInbox`, and a `_seed_handles` helper, inline equivalents in this new file rather than refactoring an existing test module:

```python
# inline fallback fixtures (put at top of test_queue_observer.py
# if reuse is awkward)
import asyncio
from aegis.tui.state import AgentState


class _FakeSession:
    def __init__(self, handle):
        self.handle = handle
        self._event_observers, self._state_observers = [], []

    def add_event_observer(self, fn): self._event_observers.append(fn)
    def add_state_observer(self, fn): self._state_observers.append(fn)

    async def complete(self, text):
        from aegis.events import AssistantText
        for fn in self._event_observers:
            fn(self, AssistantText(text=text))
        for fn in self._state_observers:
            fn(self, AgentState.ready, True)


class FakeSessionManager:
    def __init__(self):
        self.last_session = None
        self._sessions = []

    def _sync_spawn(self, profile, opening_prompt, handle):
        s = _FakeSession(handle)
        self.last_session = s
        self._sessions.append(s)
        return s

    async def close(self, handle):  # noqa: ARG002
        pass

    spawn = _sync_spawn


class FakeInbox:
    def __init__(self): self.delivered = []
    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg))


def _seed_handles(names):
    it = iter(names)
    return lambda used: next(it)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queue_observer.py -v`
Expected: FAIL — `AttributeError: 'QueueManager' object has no attribute 'subscribe'`.

- [ ] **Step 3: Implement `subscribe` + event emission**

Edit `src/aegis/queue/manager.py`:

```python
# add to imports near the top
from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueEvent,
    QueueObserver, QueueStarted, Unsubscribe,
)
```

In `QueueManager.__init__` add a `_observers` list:

```python
        self._workers: dict[str, tuple[Task, str]] = {}
        self._observers: list[QueueObserver] = []  # NEW
```

Add the `subscribe` method (place between `list_queues` and `_log`):

```python
    def subscribe(self, callback: QueueObserver) -> Unsubscribe:
        """Register an observer for every queue lifecycle transition.

        Callbacks fire after the JSONL record is committed (committed-state
        observability). Exceptions inside observers are caught and logged
        — a broken observer never poisons the substrate.
        """
        self._observers.append(callback)
        def _unsubscribe() -> None:
            with __import__("contextlib").suppress(ValueError):
                self._observers.remove(callback)
        return _unsubscribe

    def _emit(self, ev: QueueEvent) -> None:
        for cb in list(self._observers):
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                # observer bugs must not break the substrate
                import logging
                logging.getLogger(__name__).exception(
                    "queue observer raised on %s", type(ev).__name__)
```

Emit events at each transition. In `enqueue`, after `self._log(queue, {...enqueued...})`:

```python
        self._log(queue, {
            "event": "enqueued", "task_id": task.id, "queue": queue,
            "payload": payload, "enqueued_by": enqueued_by,
            "enqueued_at": task.enqueued_at, "callback": callback})
        self._emit(QueueEnqueued(  # NEW
            task_id=task.id, queue=queue,
            payload=payload, enqueued_by=enqueued_by))
        self._try_dispatch(queue)
```

In `_try_dispatch`, after the `dispatched` log, emit both `QueueDispatched` and `QueueStarted` (v1 has no separate "started" transition — dispatched and started are the same instant, but consumers benefit from the separate event for forward compatibility):

```python
            self._log(queue, {
                "event": "dispatched", "task_id": task.id,
                "worker_handle": worker_handle})
            self._emit(QueueDispatched(  # NEW
                task_id=task.id, queue=queue,
                worker_handle=worker_handle,
                agent_slug=q.agent_profile))
            self._emit(QueueStarted(  # NEW
                task_id=task.id, queue=queue))
            sync_spawn = getattr(self._sm, "_sync_spawn", self._sm.spawn)
            session = sync_spawn(...)
```

In `_finalize`, after the `completed`/`failed` log:

```python
        self._log(task.queue, {
            "event": status, "task_id": task.id,
            "result": result, "error": error,
            "completed_at": completed.completed_at})
        self._emit(QueueCompleted(  # NEW
            task_id=task.id, queue=task.queue,
            outcome=("completed" if ok else "failed"),
            result=result, error=error,
            completed_at=completed.completed_at))
```

In `_mark_interrupted` (called by `start()` for crash recovery), after the failed log:

```python
        self._log(queue, {
            "event": "failed", "task_id": tid,
            "result": None, "error": completed.error,
            "completed_at": completed.completed_at})
        self._emit(QueueCompleted(  # NEW
            task_id=tid, queue=queue,
            outcome="interrupted",
            result=None, error=completed.error,
            completed_at=completed.completed_at))
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_queue_observer.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/queue/manager.py tests/test_queue_observer.py
git commit -m "feat(queue): subscribe() observer hook on QueueManager"
git push origin main
```

---

### Task 3: `QueueDigest` snapshot service

The digest subscribes to the manager and maintains per-queue counters + per-task `TaskView`s. The strip and the dashboard both read from `QueueDigest.snapshot()`.

**Files:**
- Create: `src/aegis/queue/digest.py`
- Modify: `src/aegis/queue/__init__.py` — re-export `QueueDigest`, `QueueView`, `TaskView`.
- Test: `tests/test_queue_digest.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_queue_digest.py
from datetime import datetime, timezone

from aegis.queue.digest import QueueDigest, QueueView, TaskView
from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueStarted,
)
from aegis.queue.schema import Queue


class _StubManager:
    def __init__(self, queues):
        self._queues = {q.name: q for q in queues}
        self._cb = None

    def subscribe(self, cb):
        self._cb = cb
        return lambda: setattr(self, "_cb", None)

    def emit(self, ev):
        if self._cb is not None:
            self._cb(ev)


def _q(name, agent="claude", parallel=2):
    return Queue(name=name, agent_profile=agent, max_parallel=parallel)


def test_digest_initial_snapshot_has_zero_counts():
    sm = _StubManager([_q("tasks", "gemini-flash", 2)])
    d = QueueDigest(sm)
    d.start()

    snap = d.snapshot()
    assert snap.queues == [QueueView(
        name="tasks", agent="gemini-flash", max_parallel=2,
        running=0, queued=0, ok=0, err=0,
    )]
    assert snap.tasks == []
    assert snap.last_started is None


def test_digest_tracks_enqueue_dispatch_complete():
    sm = _StubManager([_q("tasks", "gemini-flash", 2)])
    d = QueueDigest(sm)
    d.start()

    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks",
        payload="summarize TASKS.md", enqueued_by="agent:lucid"))
    s = d.snapshot()
    assert s.queues[0].queued == 1
    assert len(s.tasks) == 1
    assert s.tasks[0].state == "queued"
    assert s.tasks[0].payload_summary == "summarize TASKS.md"

    sm.emit(QueueDispatched(
        task_id="t1", queue="tasks",
        worker_handle="brisk-curie", agent_slug="gemini-flash"))
    sm.emit(QueueStarted(task_id="t1", queue="tasks"))
    s = d.snapshot()
    assert s.queues[0].queued == 0 and s.queues[0].running == 1
    assert s.tasks[0].state == "running"
    assert s.tasks[0].worker_handle == "brisk-curie"
    assert s.last_started is not None
    assert s.last_started.task_id == "t1"

    sm.emit(QueueCompleted(
        task_id="t1", queue="tasks", outcome="completed",
        result="done", error=None,
        completed_at="2026-05-21T12:00:00Z"))
    s = d.snapshot()
    assert s.queues[0].running == 0 and s.queues[0].ok == 1
    assert s.tasks[0].state == "ok"


def test_digest_err_and_interrupted_counts_in_err():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    for tid, outcome in (("t1", "failed"), ("t2", "interrupted")):
        sm.emit(QueueEnqueued(
            task_id=tid, queue="tasks", payload="p",
            enqueued_by="agent:c"))
        sm.emit(QueueDispatched(
            task_id=tid, queue="tasks",
            worker_handle=f"w-{tid}", agent_slug="claude"))
        sm.emit(QueueStarted(task_id=tid, queue="tasks"))
        sm.emit(QueueCompleted(
            task_id=tid, queue="tasks", outcome=outcome,
            result=None, error="boom",
            completed_at="2026-05-21T12:00:00Z"))
    s = d.snapshot()
    assert s.queues[0].err == 2


def test_digest_recent_keeps_last_n_in_reverse_time_order():
    sm = _StubManager([_q("tasks", parallel=10)])
    d = QueueDigest(sm)
    d.start()
    for i in range(15):
        sm.emit(QueueEnqueued(
            task_id=f"t{i}", queue="tasks", payload=f"p{i}",
            enqueued_by="agent:c"))
        sm.emit(QueueDispatched(
            task_id=f"t{i}", queue="tasks",
            worker_handle=f"w{i}", agent_slug="claude"))
        sm.emit(QueueStarted(task_id=f"t{i}", queue="tasks"))
        sm.emit(QueueCompleted(
            task_id=f"t{i}", queue="tasks", outcome="completed",
            result="ok", error=None,
            completed_at=f"2026-05-21T12:00:{i:02d}Z"))
    s = d.snapshot()
    completed = [t for t in s.tasks if t.state in ("ok", "err")]
    assert len(completed) == 10  # default cap
    assert completed[0].task_id == "t14"  # newest first


def test_digest_unsubscribes_on_stop():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    d.stop()
    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks", payload="p",
        enqueued_by="agent:c"))
    assert d.snapshot().queues[0].queued == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queue_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.queue.digest`.

- [ ] **Step 3: Implement `QueueDigest`**

```python
# src/aegis/queue/digest.py
"""QueueDigest — push-based aggregator over QueueManager events.

Maintains in-memory per-queue counters plus a windowed task list.
The strip and the dashboard both read snapshots from this single
source of truth so they never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueEvent,
    QueueStarted,
)

RECENT_CAP = 10


@dataclass(frozen=True)
class QueueView:
    name: str
    agent: str
    max_parallel: int
    running: int
    queued: int
    ok: int
    err: int


@dataclass(frozen=True)
class TaskView:
    task_id: str
    queue: str
    state: str   # "queued" | "running" | "ok" | "err"
    payload_summary: str
    worker_handle: str | None
    agent_slug: str | None
    from_sender: str
    enqueued_at: str | None
    dispatched_at: str | None
    started_at: str | None
    completed_at: str | None
    result: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Snapshot:
    queues: list[QueueView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)
    last_started: TaskView | None = None


def _payload_summary(payload: str, limit: int = 64) -> str:
    first = next((ln for ln in payload.splitlines() if ln.strip()), "")
    return (first if len(first) <= limit
            else first[: limit - 1].rstrip() + "…")


class QueueDigest:
    def __init__(self, manager) -> None:
        self._manager = manager
        self._unsub = None
        self._tasks: dict[str, TaskView] = {}
        # ordered task ids — insertion order = enqueue order
        self._order: list[str] = []
        self._counters: dict[str, dict[str, int]] = {}
        # cache of queue config (immutable for the session)
        self._queues: list[QueueView] = []
        # last task that transitioned to running
        self._last_started: TaskView | None = None

    def start(self) -> None:
        # Hydrate queue config from manager
        cfg = getattr(self._manager, "_queues", {})
        for name in sorted(cfg):
            q = cfg[name]
            self._counters[name] = {"ok": 0, "err": 0}
            self._queues.append(QueueView(
                name=q.name, agent=q.agent_profile,
                max_parallel=q.max_parallel,
                running=0, queued=0, ok=0, err=0))
        self._unsub = self._manager.subscribe(self._on_event)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def snapshot(self) -> Snapshot:
        # Recompute per-queue live counts from task state
        running = {q.name: 0 for q in self._queues}
        queued = {q.name: 0 for q in self._queues}
        for t in self._tasks.values():
            if t.queue not in running:
                continue
            if t.state == "running":
                running[t.queue] += 1
            elif t.state == "queued":
                queued[t.queue] += 1
        queues = [
            replace(
                q, running=running[q.name], queued=queued[q.name],
                ok=self._counters[q.name]["ok"],
                err=self._counters[q.name]["err"])
            for q in self._queues
        ]
        # Visible task list: queued + running first (any order), then
        # completed in reverse time, capped at RECENT_CAP.
        active = [self._tasks[tid] for tid in self._order
                  if self._tasks[tid].state in ("queued", "running")]
        finished = [self._tasks[tid] for tid in self._order
                    if self._tasks[tid].state in ("ok", "err")]
        finished_recent = list(reversed(finished))[:RECENT_CAP]
        return Snapshot(
            queues=queues,
            tasks=finished_recent + active,
            last_started=self._last_started,
        )

    def _on_event(self, ev: QueueEvent) -> None:
        if isinstance(ev, QueueEnqueued):
            self._tasks[ev.task_id] = TaskView(
                task_id=ev.task_id, queue=ev.queue,
                state="queued",
                payload_summary=_payload_summary(ev.payload),
                worker_handle=None, agent_slug=None,
                from_sender=ev.enqueued_by,
                enqueued_at=None,
                dispatched_at=None, started_at=None,
                completed_at=None)
            self._order.append(ev.task_id)
        elif isinstance(ev, QueueDispatched):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            self._tasks[ev.task_id] = replace(
                t, worker_handle=ev.worker_handle,
                agent_slug=ev.agent_slug)
        elif isinstance(ev, QueueStarted):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            running = replace(t, state="running")
            self._tasks[ev.task_id] = running
            self._last_started = running
        elif isinstance(ev, QueueCompleted):
            t = self._tasks.get(ev.task_id)
            if t is None:
                return
            state = "ok" if ev.outcome == "completed" else "err"
            self._tasks[ev.task_id] = replace(
                t, state=state, completed_at=ev.completed_at,
                result=ev.result, error=ev.error)
            bucket = "ok" if state == "ok" else "err"
            if t.queue in self._counters:
                self._counters[t.queue][bucket] += 1
```

Then re-export from `src/aegis/queue/__init__.py`:

```python
# add to existing exports
from aegis.queue.digest import QueueDigest, QueueView, TaskView, Snapshot
from aegis.queue.events import (
    QueueCompleted, QueueDispatched, QueueEnqueued, QueueEvent,
    QueueObserver, QueueStarted, Unsubscribe,
)
```

(If `__init__.py` already uses `__all__`, append these names to it.)

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_queue_digest.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/queue/digest.py src/aegis/queue/__init__.py \
        tests/test_queue_digest.py
git commit -m "feat(queue): QueueDigest snapshot aggregator"
git push origin main
```

---

### Task 4: `QueueStrip` widget (renderer only, no Textual yet)

The strip renderer is pure: snapshot in, Rich `Text` out. We unit-test it in isolation before mounting it in the Textual tree.

**Files:**
- Create: `src/aegis/tui/strip.py`
- Test: `tests/test_tui_strip.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui_strip.py
from aegis.queue.digest import QueueView, Snapshot, TaskView
from aegis.tui.strip import render_strip
from aegis.tui.themes import aegis_colors, INK

PAL = aegis_colors(INK)


def _qv(name="tasks", agent="gemini-flash", parallel=2,
        running=0, queued=0, ok=0, err=0):
    return QueueView(
        name=name, agent=agent, max_parallel=parallel,
        running=running, queued=queued, ok=ok, err=err)


def _tv(handle="brisk-curie", state="running"):
    return TaskView(
        task_id="t1", queue="tasks", state=state,
        payload_summary="summarize TASKS.md",
        worker_handle=handle, agent_slug="gemini-flash",
        from_sender="agent:lucid",
        enqueued_at=None, dispatched_at=None,
        started_at=None, completed_at=None)


def test_strip_hidden_when_no_queues():
    snap = Snapshot(queues=[], tasks=[], last_started=None)
    assert render_strip(snap, PAL).plain == ""


def test_strip_single_queue_format():
    snap = Snapshot(
        queues=[_qv(running=1, queued=3, ok=14, err=2)],
        tasks=[],
        last_started=_tv())
    txt = render_strip(snap, PAL).plain
    assert "tasks" in txt
    assert "●1/2" in txt
    assert "○3" in txt
    assert "✓14" in txt and "✗2" in txt
    assert "last:" in txt and "brisk-curie" in txt


def test_strip_two_queues_compact_format():
    snap = Snapshot(
        queues=[
            _qv(name="tasks", running=1, queued=3),
            _qv(name="impl",  running=0, queued=0, parallel=1),
        ],
        tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "tasks" in txt and "impl" in txt
    assert "●1/2" in txt and "●0/1" in txt


def test_strip_four_plus_queues_aggregate():
    snap = Snapshot(
        queues=[_qv(name=f"q{i}", parallel=2, running=(1 if i < 3 else 0))
                for i in range(5)],
        tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "5 queues" in txt
    assert "●3/10" in txt


def test_strip_no_running_omits_last():
    snap = Snapshot(
        queues=[_qv(queued=2)], tasks=[], last_started=None)
    txt = render_strip(snap, PAL).plain
    assert "last:" not in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_strip.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.tui.strip`.

- [ ] **Step 3: Implement the renderer**

```python
# src/aegis/tui/strip.py
"""QueueStrip — always-on, one-line queue summary above the status bar.

Two pieces:
* ``render_strip(snapshot, palette)`` — pure Rich Text renderer.
* ``QueueStrip`` — Textual Static widget that subscribes to a digest
  and re-renders on each event.
"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from aegis.queue.digest import QueueDigest, QueueView, Snapshot


def _format_q(q: QueueView, palette) -> Text:
    t = Text()
    t.append(q.name, style=palette.ink)
    t.append(f" ●{q.running}", style=palette.work)
    t.append(f"/{q.max_parallel}", style=palette.muted)
    if q.queued:
        t.append(f" ○{q.queued}", style=palette.muted)
    if q.ok:
        t.append(f" ✓{q.ok}", style=palette.ok)
    if q.err:
        t.append(f" ✗{q.err}", style=palette.err)
    return t


def render_strip(snap: Snapshot, palette) -> Text:
    if not snap.queues:
        return Text("")
    line = Text()
    line.append("queues: ", style=palette.muted)
    n = len(snap.queues)
    if n <= 3:
        for i, q in enumerate(snap.queues):
            if i:
                line.append(" · ", style=palette.muted)
            line.append_text(_format_q(q, palette))
    else:
        total_running = sum(q.running for q in snap.queues)
        total_cap = sum(q.max_parallel for q in snap.queues)
        total_queued = sum(q.queued for q in snap.queues)
        total_ok = sum(q.ok for q in snap.queues)
        total_err = sum(q.err for q in snap.queues)
        line.append(f"{n} queues · ", style=palette.ink)
        line.append(f"●{total_running}/{total_cap}", style=palette.work)
        if total_queued:
            line.append(f" ○{total_queued}", style=palette.muted)
        if total_ok:
            line.append(f" ✓{total_ok}", style=palette.ok)
        if total_err:
            line.append(f" ✗{total_err}", style=palette.err)

    last = snap.last_started
    # Only show "last:" if there's still a running worker — once it
    # finishes we drop the cell rather than implying staleness.
    last_running = (last is not None and last.state == "running"
                    and last.worker_handle)
    if last_running:
        line.append("    last: ", style=palette.muted)
        line.append(last.worker_handle, style=palette.work)
    return line


class QueueStrip(Static):
    """One-row strip widget. Hidden (height: 0) when there are no
    queues; one row otherwise.
    """
    DEFAULT_CSS = """
    QueueStrip { height: 1; padding: 0 2; background: $panel;
                 color: $foreground; }
    QueueStrip.-empty { display: none; }
    """

    def __init__(self, digest: QueueDigest, palette) -> None:
        super().__init__("", id="queue-strip")
        self._digest = digest
        self._palette = palette
        self._unsub = None

    def set_palette(self, palette) -> None:
        self._palette = palette
        self._refresh()

    def on_mount(self) -> None:
        self._unsub = self._digest._manager.subscribe(
            lambda ev: self._refresh())
        self._refresh()

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _refresh(self) -> None:
        snap = self._digest.snapshot()
        if not snap.queues:
            self.add_class("-empty")
            self.update("")
            return
        self.remove_class("-empty")
        self.update(render_strip(snap, self._palette))
```

- [ ] **Step 4: Run the renderer tests**

Run: `uv run pytest tests/test_tui_strip.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/strip.py tests/test_tui_strip.py
git commit -m "feat(tui): QueueStrip widget + pure renderer"
git push origin main
```

---

### Task 5: Mount `QueueStrip` in `ConversationPane`

The strip lives between the transcript and the StatusBar, in every conversation pane. It needs a `QueueDigest` passed in.

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_tui.py` (extend with a strip-mount test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui.py` (after existing tests; if test_tui.py is empty/different, create the test inline at top of file):

```python
# tests/test_tui.py (extend)
import pytest

from aegis.config import Agent, ClaudeCode
from aegis.queue.digest import QueueDigest
from aegis.queue.schema import Queue
from aegis.tui.strip import QueueStrip


class _FakeManager:
    def __init__(self): self._queues = {
        "tasks": Queue("tasks", "claude", 2)}
    def subscribe(self, cb): return lambda: None


@pytest.mark.asyncio
async def test_pane_mounts_queue_strip(make_pane):
    digest = QueueDigest(_FakeManager()); digest.start()
    pane = await make_pane(digest=digest)
    assert pane.query(QueueStrip)
```

Where `make_pane` is a fixture used elsewhere in `test_tui.py` for mounting a `ConversationPane` against a `Textual.App.run_test`. If no such fixture exists, add a minimal one in `tests/conftest.py`:

```python
# tests/conftest.py (add if not present)
import pytest

from aegis.config import Agent, ClaudeCode
from aegis.tui.app import AegisApp
from aegis.tui.themes import aegis_colors, INK
from aegis.tui.pane import ConversationPane


@pytest.fixture
async def make_pane():
    """Returns an async builder of ConversationPane mounted under
    a minimal Textual App for run_test purposes."""
    pass  # implement only if tests need it; otherwise inline.
```

If a fixture-based approach is too heavy, write the test as a direct widget construction check (we only need `QueueStrip` to be referenced, not fully rendered). Simpler alternative test:

```python
def test_pane_holds_digest_reference():
    from aegis.queue.digest import QueueDigest
    from aegis.queue.schema import Queue
    # Construct a digest; pane will pull the digest off the app in
    # _mount_strip, which we exercise in the live integration test.
    digest = QueueDigest(_FakeManager()); digest.start()
    # ConversationPane signature should accept a digest kwarg.
    sig = ConversationPane.__init__.__code__.co_varnames
    assert "digest" in sig
```

Prefer the second, simpler shape (signature inspection) — it doesn't require Textual's `run_test`, exercises the constructor surface that downstream tests depend on, and avoids a heavier fixture only this one test would use. A later behavioural test (Task 7+) covers the Textual mount via `app.run_test`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v -k strip`
Expected: FAIL — `ConversationPane` doesn't accept a `digest` argument yet.

- [ ] **Step 3: Modify `ConversationPane`**

Edit `src/aegis/tui/pane.py`:

```python
# new import at top
from aegis.tui.strip import QueueStrip
```

Change the constructor signature:

```python
    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette,
                 *, digest=None) -> None:   # NEW: digest kwarg
        super().__init__(id=f"pane-{handle}")
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self.unseen = False
        self._digest = digest                # NEW
        self._core = AgentSession(session, agent, agent_slug, handle)
        # ... rest unchanged
```

Mount the strip in `compose()` between the transcript and the StatusBar:

```python
    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="transcript")
            if self._digest is not None:
                yield QueueStrip(self._digest, self._palette)
            yield StatusBar(self.handle, self.agent_slug,
                            self._agent.model,
                            self._agent.permission.value, self._palette)
            yield Input(placeholder="type a message…")
```

Add a small set_palette propagation note: `set_palette` should also call `QueueStrip.set_palette` if mounted. Find the existing `set_palette` and extend:

```python
    def set_palette(self, palette) -> None:
        self._palette = palette
        # propagate to children if mounted
        for w in self.query(QueueStrip):
            w.set_palette(palette)
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui.py -v -k strip`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/pane.py tests/test_tui.py tests/conftest.py
git commit -m "feat(tui): mount QueueStrip in every ConversationPane"
git push origin main
```

---

### Task 6: Wire `QueueDigest` in `AegisApp`

The app constructs one `QueueDigest`, starts it before the first spawn (so it sees every event), and passes it to every newly created `ConversationPane`. The `_SessionManagerAdapter.spawn` path is where panes are constructed during `QueueManager._try_dispatch`, so we need to plumb the digest through there too.

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_tui.py` (or `tests/test_queue_e2e.py` if a small e2e fits there)

- [ ] **Step 1: Read the relevant section of `app.py`**

The `__init__`, `compose`, `_spawn_pane` (or similarly named pane-creation helper), and `_SessionManagerAdapter.spawn` are the call sites that need the digest.

- [ ] **Step 2: Write a failing wiring test**

```python
# tests/test_tui.py (extend)
def test_app_constructs_digest():
    from aegis.tui.app import AegisApp
    from aegis.config import Agent, ClaudeCode

    def make_session(agent, slug, handle):
        raise NotImplementedError

    class _DummyMCP:
        def bind(self, _): pass
        async def start(self): pass

    app = AegisApp(
        agents={"claude": Agent(provider=ClaudeCode(model="opus"))},
        default_agent="claude",
        make_session=make_session,
        mcp=_DummyMCP(),
        queues={"tasks": __import__(
            "aegis.queue.schema", fromlist=["Queue"]
        ).Queue("tasks", "claude", 2)})
    assert hasattr(app, "queue_digest")
    assert app.queue_digest is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v -k digest`
Expected: FAIL — `AegisApp` has no `queue_digest` attribute.

- [ ] **Step 4: Implement the wiring**

In `AegisApp.__init__`:

```python
# add import at top
from aegis.queue import QueueDigest

# inside __init__, after QueueManager is constructed:
        self.queue_manager = QueueManager(
            self._queues, _SessionManagerAdapter(self), self.inbox_router)
        self.queue_digest = QueueDigest(self.queue_manager)  # NEW
        self.queue_digest.start()                             # NEW
        self._mcp.bind(self)
```

Find every place that constructs a `ConversationPane`. Pass `digest=self.queue_digest`. There are typically two call sites: an interactive "new tab" path and the `_SessionManagerAdapter.spawn` worker-tab path. Both pane constructors get `digest=app.queue_digest`.

If `_SessionManagerAdapter.spawn` is the place worker panes are mounted, edit it to pass `digest=self._app.queue_digest` to `ConversationPane(...)`.

- [ ] **Step 5: Run the new test**

Run: `uv run pytest tests/test_tui.py -v -k digest`
Expected: PASS.

- [ ] **Step 6: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/app.py tests/test_tui.py
git commit -m "feat(tui): wire QueueDigest into AegisApp and panes"
git push origin main
```

**Slice 1 acceptance check** — manual: launch `aegis` (interactive) inside `repos/aegis`, then from a second shell call `aegis_enqueue("tasks", "payload", from_handle="agent:test", callback=False)` via the MCP plane (or run an `aegis_enqueue` from inside a session). The strip in every pane should immediately show `queues: tasks ●1/2 ○0 …`. Tear down with Ctrl+Q.

---

## Slice 2 — modal dashboard with all four bands

**End-of-slice acceptance:** `Ctrl+D` from any conversation opens a modal showing `QUEUES / IN-FLIGHT / QUEUED / RECENT` populated from the digest. `Esc` dismisses. No cursor or detail panel yet.

### Task 7: `QueueDashboard` ModalScreen skeleton

**Files:**
- Create: `src/aegis/tui/dashboard.py`
- Test: `tests/test_tui_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_dashboard.py
import pytest
from textual.app import App
from textual.widgets import Label

from aegis.queue.digest import QueueDigest, Snapshot, QueueView, TaskView
from aegis.queue.schema import Queue
from aegis.tui.dashboard import QueueDashboard


class _FakeManager:
    def __init__(self):
        self._queues = {"tasks": Queue("tasks", "claude", 2)}
    def subscribe(self, cb): return lambda: None


class _Harness(App):
    def __init__(self, digest, session_manager):
        super().__init__()
        self.queue_digest = digest
        self.session_manager = session_manager

    def compose(self):
        yield Label("home")

    async def on_mount(self):
        await self.push_screen(QueueDashboard())


class _SM:
    def get(self, handle): return None
    def focus(self, handle): pass


@pytest.mark.asyncio
async def test_dashboard_pushes_and_dismisses():
    digest = QueueDigest(_FakeManager()); digest.start()
    app = _Harness(digest, _SM())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Dashboard is on top of the stack
        assert isinstance(app.screen, QueueDashboard)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, QueueDashboard)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the modal skeleton**

```python
# src/aegis/tui/dashboard.py
"""QueueDashboard — modal observability surface for the queue substrate."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class QueueDashboard(ModalScreen):
    CSS = """
    QueueDashboard { align: center middle; background: $background; }
    QueueDashboard #wrap { width: 100%; height: 100%;
                           background: $background; padding: 1 2; }
    QueueDashboard #left  { width: 2fr; height: 1fr; }
    QueueDashboard #right { width: 1fr; height: 1fr;
                            border-left: solid $foreground 20%;
                            padding-left: 2; }
    QueueDashboard #footer { dock: bottom; height: 1;
                             color: $foreground 60%; padding: 0 2; }
    """
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="wrap"):
            with Horizontal():
                with Vertical(id="left"):
                    yield Static("QUEUES", id="band-queues")
                    yield Static("IN-FLIGHT", id="band-inflight")
                    yield Static("QUEUED", id="band-queued")
                    yield Static("RECENT", id="band-recent")
                with Vertical(id="right"):
                    yield Static("DETAIL", id="detail")
            yield Static(
                "↑↓ select  enter focus  > jump to tab  esc collapse",
                id="footer")

    def action_dismiss(self) -> None:
        self.dismiss()
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui_dashboard.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py
git commit -m "feat(tui): QueueDashboard modal skeleton"
git push origin main
```

---

### Task 8: Bind `Ctrl+D` in `AegisApp` to open dashboard

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: `tests/test_tui.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui.py (extend)
import pytest

from aegis.tui.dashboard import QueueDashboard


@pytest.mark.asyncio
async def test_ctrl_d_opens_dashboard(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert isinstance(app.screen, QueueDashboard)
```

`make_app` is whatever existing factory `test_tui.py` uses to build an `AegisApp` for `run_test`. If none exists, inline:

```python
@pytest.fixture
def make_app():
    from aegis.tui.app import AegisApp
    from aegis.config import Agent, ClaudeCode
    from aegis.queue.schema import Queue

    class _DummyMCP:
        def bind(self, _): pass
        async def start(self): pass

    def _factory():
        return AegisApp(
            agents={"claude": Agent(provider=ClaudeCode(model="opus"))},
            default_agent="claude",
            make_session=lambda *a, **kw: (_ for _ in ()).throw(
                NotImplementedError),
            mcp=_DummyMCP(),
            queues={"tasks": Queue("tasks", "claude", 2)})
    return _factory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v -k ctrl_d`
Expected: FAIL — `Ctrl+D` isn't bound.

- [ ] **Step 3: Add the binding + action**

In `AegisApp.BINDINGS` add:

```python
        Binding("ctrl+d", "open_dashboard", "Queues", priority=True),
```

Add the action method on `AegisApp`:

```python
    async def action_open_dashboard(self) -> None:
        from aegis.tui.dashboard import QueueDashboard
        await self.push_screen(QueueDashboard())
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui.py -v -k ctrl_d`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/app.py tests/test_tui.py
git commit -m "feat(tui): bind Ctrl+D to open queue dashboard"
git push origin main
```

---

### Task 9: `QueuesBand` widget (config + counts)

The QUEUES band is a small static block. We give it its own widget so it can subscribe to the digest and refresh independently of the other bands.

**Files:**
- Modify: `src/aegis/tui/dashboard.py` (add `QueuesBand` class + use it)
- Test: `tests/test_tui_dashboard.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_dashboard.py (extend)
import pytest

from aegis.queue.events import (
    QueueDispatched, QueueEnqueued, QueueStarted,
)


@pytest.mark.asyncio
async def test_queues_band_renders_config_and_counts(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        # Emit one enqueue + dispatch via the fake manager
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks", payload="p",
            enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="w1", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        rendered = app.screen.query_one("#band-queues").renderable.plain
        assert "tasks" in rendered
        assert "agent claude" in rendered
        assert "parallel 2" in rendered
        assert "running 1" in rendered
```

`make_dashboard_app` returns an `(app, fake_manager)` pair where the app's `queue_digest` is wired to `fake_manager`. Define this fixture in `tests/conftest.py` if not present.

- [ ] **Step 2: Run the new test, expect failure**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k queues_band`
Expected: FAIL — band still renders the literal "QUEUES" placeholder.

- [ ] **Step 3: Implement `QueuesBand`**

In `src/aegis/tui/dashboard.py`:

```python
from rich.text import Text
from textual.widget import Widget

from aegis.queue.digest import QueueDigest, QueueView


class _Band(Widget):
    """Common scaffold: subscribe to digest, re-render on every event."""
    DEFAULT_CSS = """
    _Band { height: auto; padding: 0; margin-bottom: 1; }
    """

    def __init__(self, digest: QueueDigest, palette) -> None:
        super().__init__()
        self._digest = digest
        self._palette = palette
        self._unsub = None
        self._inner = Static("")

    def compose(self) -> ComposeResult:
        yield self._inner

    def on_mount(self) -> None:
        self._unsub = self._digest._manager.subscribe(
            lambda ev: self.refresh_render())
        self.refresh_render()

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def refresh_render(self) -> None:
        raise NotImplementedError


class QueuesBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        pal = self._palette
        t = Text()
        t.append("QUEUES\n", style=f"bold {pal.accent}")
        for q in snap.queues:
            t.append(f"\n  {q.name}\n", style=pal.ink)
            t.append("    agent ", style=pal.muted)
            t.append(q.agent, style=pal.accent)
            t.append(" · parallel ", style=pal.muted)
            t.append(f"{q.max_parallel}\n", style=pal.ink)
            t.append("    running ", style=pal.muted)
            t.append(f"{q.running}", style=pal.work)
            t.append(" · queued ", style=pal.muted)
            t.append(f"{q.queued}", style=pal.work)
            if q.ok:
                t.append(" · ", style=pal.muted)
                t.append(f"✓{q.ok}", style=pal.ok)
            if q.err:
                t.append(" ", style=pal.muted)
                t.append(f"✗{q.err}", style=pal.err)
        self._inner.update(t)
```

Update `QueueDashboard.compose()` to use `QueuesBand` instead of the literal `Static("QUEUES")`. The dashboard needs access to the app's `queue_digest`:

```python
    def compose(self) -> ComposeResult:
        digest = self.app.queue_digest
        palette = self.app.palette
        with Vertical(id="wrap"):
            with Horizontal():
                with Vertical(id="left"):
                    yield QueuesBand(digest, palette)
                    yield Static("IN-FLIGHT", id="band-inflight")
                    yield Static("QUEUED",    id="band-queued")
                    yield Static("RECENT",    id="band-recent")
                with Vertical(id="right"):
                    yield Static("DETAIL", id="detail")
            yield Static(
                "↑↓ select  enter focus  > jump to tab  esc collapse",
                id="footer")
```

Adjust the test's selector if needed. If the test queries `#band-queues`, give the band that id (`QueuesBand(... , id="band-queues")` — pass `id` through `super().__init__(id=...)` in `_Band`).

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k queues_band`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py \
        tests/conftest.py
git commit -m "feat(tui): QueuesBand renders config + live counts"
git push origin main
```

---

### Task 10: `InFlightBand`, `QueuedBand`, `RecentBand`

Three task-row bands. Each filters the snapshot's `tasks` list by state and renders one line per task.

**Files:**
- Modify: `src/aegis/tui/dashboard.py`
- Test: `tests/test_tui_dashboard.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui_dashboard.py (extend)
@pytest.mark.asyncio
async def test_inflight_band_lists_running_tasks(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks",
            payload="summarize TASKS.md", enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="brisk-curie", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        rendered = app.screen.query_one("#band-inflight").renderable.plain
        assert "brisk-curie" in rendered
        assert "summarize TASKS.md" in rendered


@pytest.mark.asyncio
async def test_queued_band_lists_pending_tasks(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        for i in range(3):
            manager.emit(QueueEnqueued(
                task_id=f"q{i}", queue="tasks",
                payload=f"payload {i}", enqueued_by="agent:c"))
        await pilot.pause()
        rendered = app.screen.query_one("#band-queued").renderable.plain
        for i in range(3):
            assert f"payload {i}" in rendered


@pytest.mark.asyncio
async def test_recent_band_shows_completed_in_reverse_time(
        make_dashboard_app):
    from aegis.queue.events import QueueCompleted
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        for i, outcome in enumerate(["completed", "failed", "completed"]):
            manager.emit(QueueEnqueued(
                task_id=f"r{i}", queue="tasks",
                payload=f"p {i}", enqueued_by="agent:c"))
            manager.emit(QueueDispatched(
                task_id=f"r{i}", queue="tasks",
                worker_handle=f"w{i}", agent_slug="claude"))
            manager.emit(QueueStarted(task_id=f"r{i}", queue="tasks"))
            manager.emit(QueueCompleted(
                task_id=f"r{i}", queue="tasks", outcome=outcome,
                result=None, error=None,
                completed_at=f"2026-05-21T12:00:0{i}Z"))
        await pilot.pause()
        rendered = app.screen.query_one("#band-recent").renderable.plain
        # newest first
        idx0 = rendered.index("p 0")
        idx2 = rendered.index("p 2")
        assert idx2 < idx0
```

- [ ] **Step 2: Run the new tests, expect failures**

Run: `uv run pytest tests/test_tui_dashboard.py -v`
Expected: 3 new failures.

- [ ] **Step 3: Implement the three bands**

Append to `src/aegis/tui/dashboard.py`:

```python
def _format_task_row(t, palette, mode: str) -> Text:
    """One-line task row. mode is 'inflight' | 'queued' | 'recent'."""
    pal = palette
    line = Text()
    if mode == "inflight":
        line.append(" ● ", style=pal.work)
        line.append(t.worker_handle or "—", style=pal.ink)
    elif mode == "queued":
        line.append(" ○ —          ", style=pal.muted)
    else:  # recent
        glyph, style = (("✓", pal.ok) if t.state == "ok"
                        else ("✗", pal.err))
        line.append(f" {glyph} ", style=style)
        line.append(
            (t.worker_handle or "—").ljust(14)[:14], style=pal.muted)
    line.append(f"  {t.queue:<8}", style=pal.muted)
    line.append(f"  {t.payload_summary}", style=pal.ink)
    line.append("\n")
    return line


class InFlightBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        running = [t for t in snap.tasks if t.state == "running"]
        t = Text()
        t.append("IN-FLIGHT\n", style=f"bold {self._palette.accent}")
        if not running:
            t.append("  (none)\n", style=self._palette.muted)
        for row in running:
            t.append_text(_format_task_row(row, self._palette, "inflight"))
        self._inner.update(t)


class QueuedBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        queued = [x for x in snap.tasks if x.state == "queued"]
        t = Text()
        t.append("QUEUED\n", style=f"bold {self._palette.accent}")
        if not queued:
            t.append("  (none)\n", style=self._palette.muted)
        for row in queued:
            t.append_text(_format_task_row(row, self._palette, "queued"))
        self._inner.update(t)


class RecentBand(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        recent = [x for x in snap.tasks if x.state in ("ok", "err")]
        t = Text()
        t.append("RECENT\n", style=f"bold {self._palette.accent}")
        if not recent:
            t.append("  (none)\n", style=self._palette.muted)
        for row in recent:
            t.append_text(_format_task_row(row, self._palette, "recent"))
        self._inner.update(t)
```

Replace the `Static("IN-FLIGHT", id="band-inflight")` etc. lines in `QueueDashboard.compose()`:

```python
                with Vertical(id="left"):
                    yield QueuesBand(digest, palette, id="band-queues")
                    yield InFlightBand(digest, palette, id="band-inflight")
                    yield QueuedBand(digest, palette, id="band-queued")
                    yield RecentBand(digest, palette, id="band-recent")
```

Make sure `_Band.__init__` forwards an optional `id`:

```python
    def __init__(self, digest, palette, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._digest = digest
        ...
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_tui_dashboard.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py
git commit -m "feat(tui): InFlightBand + QueuedBand + RecentBand"
git push origin main
```

**Slice 2 acceptance** — manual: enqueue a few tasks, `Ctrl+D` shows them grouped by state, `Esc` dismisses.

---

## Slice 3 — selection + detail panel + live tail

**End-of-slice acceptance:** `↑/↓` move a cursor across the three task bands; the right-side `DetailPanel` shows the selected task's payload, lifecycle timestamps, and (for running tasks) a live tail of assistant text.

### Task 11: Selection state + cursor rendering

**Files:**
- Modify: `src/aegis/tui/dashboard.py`
- Test: `tests/test_tui_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_arrow_keys_move_cursor(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        # Two queued tasks
        for i in range(2):
            manager.emit(QueueEnqueued(
                task_id=f"t{i}", queue="tasks",
                payload=f"p{i}", enqueued_by="agent:c"))
        await pilot.pause()
        screen = app.screen
        # selection starts at first task
        assert screen.selected_task_id == "t0"
        await pilot.press("down"); await pilot.pause()
        assert screen.selected_task_id == "t1"
        await pilot.press("up"); await pilot.pause()
        assert screen.selected_task_id == "t0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k arrow_keys`
Expected: FAIL — no `selected_task_id` attribute on the screen.

- [ ] **Step 3: Add selection state**

In `QueueDashboard`:

```python
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("up",    "cursor_prev",  "Up",      priority=True),
        Binding("down",  "cursor_next",  "Down",    priority=True),
        Binding("enter", "refresh_detail", "Refresh", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected_task_id: str | None = None

    def action_refresh_detail(self) -> None:
        # Per-spec: focus / refresh the detail panel for the selected
        # row. Cursor moves auto-refresh, so Enter is a manual force.
        self._refresh_bands()

    def _ordered_task_ids(self) -> list[str]:
        snap = self.app.queue_digest.snapshot()
        order = []
        # band order: in-flight, queued, recent
        order += [t.task_id for t in snap.tasks if t.state == "running"]
        order += [t.task_id for t in snap.tasks if t.state == "queued"]
        order += [t.task_id for t in snap.tasks if t.state in ("ok", "err")]
        return order

    def _ensure_selection(self) -> None:
        ids = self._ordered_task_ids()
        if not ids:
            self.selected_task_id = None
            return
        if self.selected_task_id not in ids:
            self.selected_task_id = ids[0]

    def action_cursor_next(self) -> None:
        self._ensure_selection()
        ids = self._ordered_task_ids()
        if not ids:
            return
        i = ids.index(self.selected_task_id)
        self.selected_task_id = ids[(i + 1) % len(ids)]
        self._refresh_bands()

    def action_cursor_prev(self) -> None:
        self._ensure_selection()
        ids = self._ordered_task_ids()
        if not ids:
            return
        i = ids.index(self.selected_task_id)
        self.selected_task_id = ids[(i - 1) % len(ids)]
        self._refresh_bands()

    def _refresh_bands(self) -> None:
        for w in self.query("._Band"):
            w.refresh_render()
```

Add CSS class to `_Band`:

```python
class _Band(Widget):
    DEFAULT_CSS = """
    _Band { height: auto; padding: 0; margin-bottom: 1; }
    """
    DEFAULT_CLASSES = "_Band"   # so query("._Band") finds them
```

Pass selection into `_format_task_row`:

In each band's `refresh_render`, change:

```python
        screen = self.screen  # the QueueDashboard
        selected = getattr(screen, "selected_task_id", None)
        for row in queued:
            t.append_text(_format_task_row(
                row, self._palette, "queued",
                selected=(row.task_id == selected)))
```

And update `_format_task_row` to take a `selected` kwarg, prepending `▶ ` and using a reverse style when selected:

```python
def _format_task_row(t, palette, mode: str,
                     *, selected: bool = False) -> Text:
    pal = palette
    line = Text()
    cursor = "▶" if selected else " "
    line.append(f"{cursor} ", style=pal.accent if selected else pal.muted)
    # rest unchanged …
```

Also pre-pend initial selection on `on_mount` — call `self._ensure_selection()` once after the bands subscribe.

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k arrow_keys`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py
git commit -m "feat(tui): cursor selection across dashboard bands"
git push origin main
```

---

### Task 12: `DetailPanel` widget (static fields)

The detail panel shows the selected task's identity, sender, state, payload, lifecycle. Live tail is added in the next task.

**Files:**
- Modify: `src/aegis/tui/dashboard.py`
- Test: `tests/test_tui_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_detail_panel_shows_selected_task_fields(
        make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks",
            payload="summarize TASKS.md\ninto buckets",
            enqueued_by="agent:lucid"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="brisk-curie", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        detail = app.screen.query_one("#detail").renderable.plain
        assert "t1" in detail
        assert "tasks" in detail
        assert "brisk-curie" in detail
        assert "claude" in detail
        assert "summarize TASKS.md" in detail
        assert "agent:lucid" in detail
        assert "running" in detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k detail_panel`
Expected: FAIL — detail still shows the literal "DETAIL".

- [ ] **Step 3: Implement `DetailPanel`**

Replace `Static("DETAIL", id="detail")` with `DetailPanel(...)`:

```python
class DetailPanel(_Band):
    def refresh_render(self) -> None:
        snap = self._digest.snapshot()
        screen = self.screen
        sel = getattr(screen, "selected_task_id", None)
        match = next((t for t in snap.tasks if t.task_id == sel), None)
        pal = self._palette
        t = Text()
        t.append("DETAIL\n\n", style=f"bold {pal.accent}")
        if match is None:
            t.append("(no task selected)\n", style=pal.muted)
            self._inner.update(t)
            return
        t.append(f"task    ", style=pal.muted)
        t.append(f"{match.task_id}\n", style=pal.ink)
        t.append(f"queue   ", style=pal.muted)
        t.append(f"{match.queue}\n", style=pal.ink)
        t.append(f"worker  ", style=pal.muted)
        t.append(f"{match.worker_handle or '—'}\n", style=pal.ink)
        t.append(f"agent   ", style=pal.muted)
        t.append(f"{match.agent_slug or '—'}\n", style=pal.ink)
        t.append(f"from    ", style=pal.muted)
        t.append(f"{match.from_sender}\n", style=pal.ink)
        t.append(f"state   ", style=pal.muted)
        state_style = {
            "running": pal.work, "queued": pal.muted,
            "ok": pal.ok, "err": pal.err,
        }.get(match.state, pal.ink)
        t.append(f"{match.state}\n\n", style=state_style)
        t.append("payload\n", style=pal.muted)
        for line in match.payload_summary.splitlines():
            t.append(f"  {line}\n", style=pal.ink)
        t.append("\nlifecycle\n", style=pal.muted)
        t.append(f"  completed_at  {match.completed_at or '—'}\n",
                 style=pal.muted)
        self._inner.update(t)
```

(The lifecycle section currently only shows `completed_at` — the digest's `TaskView` carries the additional fields wired in Slice 1; populate them as the digest exposes them. If the digest doesn't yet expose `enqueued_at`/`dispatched_at`/`started_at`, leave them off and a follow-up will widen the dataclass — they're declared `None` defaults in the spec's data model.)

Replace the placeholder in `QueueDashboard.compose()`:

```python
                with Vertical(id="right"):
                    yield DetailPanel(digest, palette, id="detail")
```

Update `_refresh_bands` to also refresh the detail panel:

```python
    def _refresh_bands(self) -> None:
        for w in self.query("._Band"):
            w.refresh_render()
        # DetailPanel is also a _Band; the query above already includes it.
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k detail_panel`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py
git commit -m "feat(tui): DetailPanel with task identity + payload"
git push origin main
```

---

### Task 13: Live tail subscription via `SessionManager`

When the selected task is `running`, the detail panel shows the last N (default 8) lines of assistant text from the worker's `AgentSession`. The digest already records `result` on completion; for live tasks we tap the worker session directly.

**Files:**
- Modify: `src/aegis/queue/digest.py` — store per-handle tail buffer.
- Modify: `src/aegis/tui/dashboard.py` — DetailPanel renders the tail from the digest.
- Test: `tests/test_queue_digest.py` (add live-tail test), `tests/test_tui_dashboard.py` (add live-tail render test).

- [ ] **Step 1: Write the failing digest test**

```python
# tests/test_queue_digest.py (extend)
def test_digest_records_assistant_text_per_worker():
    sm = _StubManager([_q("tasks")])
    d = QueueDigest(sm)
    d.start()
    sm.emit(QueueEnqueued(
        task_id="t1", queue="tasks", payload="p",
        enqueued_by="agent:c"))
    sm.emit(QueueDispatched(
        task_id="t1", queue="tasks",
        worker_handle="brisk-curie", agent_slug="claude"))
    sm.emit(QueueStarted(task_id="t1", queue="tasks"))
    d.record_assistant_text("brisk-curie", "Reading TASKS.md…")
    d.record_assistant_text("brisk-curie", "Found 4 sections.")
    s = d.snapshot()
    tail = d.tail_of("t1")
    assert tail == ["Reading TASKS.md…", "Found 4 sections."]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_queue_digest.py -v -k assistant_text`
Expected: FAIL — no `record_assistant_text` / `tail_of`.

- [ ] **Step 3: Implement the tail buffer**

In `QueueDigest`:

```python
TAIL_CAP = 8

# add in __init__
        self._tails: dict[str, list[str]] = {}   # worker_handle -> lines

    def record_assistant_text(self, worker_handle: str, text: str) -> None:
        if not text or not worker_handle:
            return
        buf = self._tails.setdefault(worker_handle, [])
        buf.append(text)
        if len(buf) > TAIL_CAP:
            del buf[: len(buf) - TAIL_CAP]

    def tail_of(self, task_id: str) -> list[str]:
        t = self._tasks.get(task_id)
        if t is None or t.worker_handle is None:
            return []
        return list(self._tails.get(t.worker_handle, []))
```

- [ ] **Step 4: Run the digest test**

Run: `uv run pytest tests/test_queue_digest.py -v -k assistant_text`
Expected: PASS.

- [ ] **Step 5: Wire the digest into the worker spawn path**

The substrate already records `last_assistant_text` per worker in `QueueManager._attach_observers`. We add a second hook that forwards to the digest. In `_SessionManagerAdapter.spawn` (the path that creates real panes for worker tabs in `app.py`), after constructing the pane, register a per-event observer that forwards `AssistantText` events to `app.queue_digest.record_assistant_text(handle, ev.text)`.

Pseudocode for the patch in `app.py`:

```python
# inside _SessionManagerAdapter.spawn or wherever a worker pane is built
def _on_assistant(s, ev):
    from aegis.events import AssistantText
    if isinstance(ev, AssistantText) and ev.text:
        self._app.queue_digest.record_assistant_text(
            s.handle, ev.text)
session.add_event_observer(_on_assistant)
```

- [ ] **Step 6: Write the dashboard live-tail test**

```python
@pytest.mark.asyncio
async def test_detail_panel_renders_live_tail(make_dashboard_app):
    app, manager = make_dashboard_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks", payload="p",
            enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="brisk-curie", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        app.queue_digest.record_assistant_text(
            "brisk-curie", "Reading TASKS.md…")
        # Trigger a refresh — the dashboard subscribes to manager events,
        # not to assistant-text directly, so call _refresh_bands().
        app.screen._refresh_bands()
        await pilot.pause()
        detail = app.screen.query_one("#detail").renderable.plain
        assert "Reading TASKS.md…" in detail
```

- [ ] **Step 7: Render the tail in `DetailPanel.refresh_render`**

After the `payload` block:

```python
        t.append("\ntail (live)\n", style=pal.muted)
        tail = self._digest.tail_of(match.task_id)
        if not tail:
            t.append("  —\n", style=pal.muted)
        else:
            for line in tail:
                t.append(f"  {line}\n", style=pal.ink)
```

For completed tasks, prefer the captured final `result` over the tail:

```python
        if match.state in ("ok", "err"):
            t.append("\nresult\n", style=pal.muted)
            for line in (match.result or match.error or "—").splitlines():
                t.append(f"  {line}\n", style=pal.ink)
```

- [ ] **Step 8: Run the new tests**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k live_tail`
Expected: PASS.

- [ ] **Step 9: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/queue/digest.py src/aegis/tui/dashboard.py \
        src/aegis/tui/app.py tests/test_queue_digest.py \
        tests/test_tui_dashboard.py
git commit -m "feat(queue,tui): live assistant-text tail in DetailPanel"
git push origin main
```

---

## Slice 4 — jump-to-tab, edge cases, polish

**End-of-slice acceptance:** `>` jumps to the running worker's tab; dashboard handles empty/narrow/no-queues states gracefully.

### Task 14: `>` key — jump to worker's tab

**Files:**
- Modify: `src/aegis/tui/dashboard.py`
- Test: `tests/test_tui_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_jump_to_tab_focuses_worker_pane(make_dashboard_app):
    focused = []

    class _SM:
        def get(self, handle):
            return handle == "brisk-curie" or None
        def focus(self, handle):
            focused.append(handle)

    app, manager = make_dashboard_app(sm=_SM())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        manager.emit(QueueEnqueued(
            task_id="t1", queue="tasks", payload="p",
            enqueued_by="agent:c"))
        manager.emit(QueueDispatched(
            task_id="t1", queue="tasks",
            worker_handle="brisk-curie", agent_slug="claude"))
        manager.emit(QueueStarted(task_id="t1", queue="tasks"))
        await pilot.pause()
        await pilot.press("greater_than_sign")
        await pilot.pause()
        assert focused == ["brisk-curie"]
        # dashboard dismissed on jump
        assert not isinstance(app.screen, QueueDashboard)
```

The `_SM` stub gives the app a `session_manager` with `get`/`focus`. If `AegisApp` already exposes a different surface (e.g., via `_SessionManagerAdapter` or `query_one(TabBar)`), adapt the test to that surface.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k jump_to_tab`
Expected: FAIL — no binding.

- [ ] **Step 3: Add the binding + action**

```python
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("up",   "cursor_prev", "Up",   priority=True),
        Binding("down", "cursor_next", "Down", priority=True),
        Binding("greater_than_sign", "jump_to_tab", "Jump", priority=True),
    ]

    def action_jump_to_tab(self) -> None:
        snap = self.app.queue_digest.snapshot()
        sel = self.selected_task_id
        match = next((t for t in snap.tasks if t.task_id == sel), None)
        if match is None or match.worker_handle is None:
            return
        sm = getattr(self.app, "session_manager", None)
        if sm is None or sm.get(match.worker_handle) is None:
            return
        sm.focus(match.worker_handle)
        self.dismiss()
```

Make sure `AegisApp` exposes a `session_manager` attribute with a `get(handle)` / `focus(handle)` surface — the existing tab-switching code already knows how to focus a pane by handle, so this is wrapping that.

If `AegisApp` switches tabs via `query_one(TabBar).select(idx)` or similar, write a small adapter on the app:

```python
    # in AegisApp
    @property
    def session_manager(self):
        return _SessionFocusAdapter(self)


class _SessionFocusAdapter:
    def __init__(self, app):
        self._app = app
    def get(self, handle):
        for p in self._app._panes:
            if p.handle == handle:
                return p
        return None
    def focus(self, handle):
        for i, p in enumerate(self._app._panes):
            if p.handle == handle:
                self._app.query_one(ContentSwitcher).current = p.id
                return
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_tui_dashboard.py -v -k jump_to_tab`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py src/aegis/tui/app.py \
        tests/test_tui_dashboard.py
git commit -m "feat(tui): > key jumps from dashboard to worker tab"
git push origin main
```

---

### Task 15: Empty-states + no-queues + narrow-terminal

**Files:**
- Modify: `src/aegis/tui/dashboard.py`, `src/aegis/tui/strip.py`
- Test: `tests/test_tui_dashboard.py`, `tests/test_tui_strip.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tui_dashboard.py (extend)
@pytest.mark.asyncio
async def test_dashboard_no_queues_shows_message(make_dashboard_app):
    app, _ = make_dashboard_app(queues={})
    async with app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert "no queues configured" in (
            app.screen.query_one("#band-queues").renderable.plain.lower())
```

```python
# tests/test_tui_strip.py (extend)
def test_strip_renders_empty_text_when_no_queues():
    from aegis.queue.digest import Snapshot
    assert render_strip(
        Snapshot(queues=[], tasks=[], last_started=None), PAL).plain == ""
```

- [ ] **Step 2: Run tests, expect failures**

Run: `uv run pytest tests/test_tui_strip.py tests/test_tui_dashboard.py -v -k empty`
Expected: 1 new failure (the strip case may already pass from Task 4).

- [ ] **Step 3: Implement empty-state in `QueuesBand`**

In `QueuesBand.refresh_render`:

```python
        if not snap.queues:
            t.append("(no queues configured in .aegis.py)\n",
                     style=self._palette.muted)
            self._inner.update(t)
            return
```

(The strip empty-state already works from Task 4.)

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_tui_strip.py tests/test_tui_dashboard.py -v -k empty`
Expected: PASS.

- [ ] **Step 5: Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/dashboard.py tests/test_tui_dashboard.py \
        tests/test_tui_strip.py
git commit -m "feat(tui): empty-state for QueuesBand when no queues"
git push origin main
```

---

## Slice 5 — live smoke test

### Task 16: End-to-end test against real `claude`

**Files:**
- Create: `tests/test_queue_dashboard_live.py`

- [ ] **Step 1: Write the live test**

```python
# tests/test_queue_dashboard_live.py
import pytest
import shutil

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_dashboard_strip_reflects_real_enqueue(tmp_path):
    if shutil.which("claude") is None:
        pytest.skip("claude not on PATH")

    # Use the existing live-test scaffolding from test_queue_live.py:
    # set up a real AegisApp inside tmp_path, configure a single queue
    # "tasks" -> claude (effort low), call queue_manager.enqueue() with
    # a trivial payload, then assert that:
    # (a) the strip renders ●1/<parallel> within a few seconds
    # (b) after completion, ✓1 appears in the strip
    # (c) the digest snapshot's tasks list has the completed task
    ...  # implementation parallels tests/test_queue_live.py
```

Use `tests/test_queue_live.py` as the template — it already configures a real claude subprocess. The test should run < 60 seconds; if it routinely exceeds 90s, drop it or restructure.

- [ ] **Step 2: Run the live test locally**

Run: `uv run pytest tests/test_queue_dashboard_live.py -v`
Expected: PASS when `claude` is on PATH; SKIPPED otherwise.

- [ ] **Step 3: Run the full hermetic suite to verify no leakage**

Run: `uv run pytest -q -m "not live"`
Expected: PASS (and the new live test is skipped).

- [ ] **Step 4: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/test_queue_dashboard_live.py
git commit -m "test(queue-dashboard): live smoke test against real claude"
git push origin main
```

---

## Post-implementation

- [ ] Update `TASKS.md` — move "queue dashboard" from a watched item to a shipped item with a one-paragraph summary mirroring the queue-v1 section.
- [ ] Update `repos/aegis/CHANGELOG.md` with an entry under the next minor version (queue dashboard adds a user-visible TUI surface — minor bump candidate, depending on existing semver discipline).
- [ ] Update the workspace's `vault/Efforts/Repos/aegis.md` body — add a paragraph under the existing "shipped" rollup, dated 2026-05-21, describing the queue dashboard and what's now visible by default.

Verify all 175+ existing hermetic tests + new ones pass:

```
uv run pytest -q -m "not live"
```

Then verify the live suite still works:

```
uv run pytest -q -m live
```

Done.
