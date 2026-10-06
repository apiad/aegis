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

from pathlib import Path

import asyncio
from types import SimpleNamespace

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result, SystemInit
from aegis.queue import (
    InboxRouter,
    Queue,
    QueueManager,
    sender_agent,
)
from aegis.tui.state import AgentState


HANG = object()  # sentinel: harness's event stream blocks forever


class StubMonitors:
    """The monitor plane ``close_guard.gather_facts`` reads.

    Only the one method it calls. Exists so a test can put a worker into
    the state that matters most for the stall arm: ended its turn in order
    to WAIT, which must defer rather than rebuild.
    """

    def __init__(self, armed: dict[str, list]):
        self._armed = armed

    def snapshot(self, *, for_handle: str | None = None) -> list:
        return list(self._armed.get(for_handle, []))



class FakeHarness:
    def __init__(self, events):
        self._events = list(events)
        self.sent: list[str] = []
        self.started = self.closed = False
        # A real driver latches this on its first SystemInit; without the
        # attribute `AgentSession.session_id` raises, which the recovery
        # probe swallows into "not resumable".
        self.session_id = None

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
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        await asyncio.Event().wait()
        if False:  # pragma: no cover — unreachable, keeps it a generator
            yield


class StubSM:
    """A session manager for the queue's sake: spawn, close, reconnect, and
    the seams a test needs to drive a worker's turn by hand.

    ``autostart=False`` is what the recovery fixtures use. With it on, a
    spawned worker immediately runs its scripted turn to completion, which
    makes ``fail()`` unreachable — the task is already terminal by the time
    a test can end its turn badly.
    """

    def __init__(self, *, autostart: bool = True):
        self._sessions: list[AgentSession] = []
        self._scripts: dict[str, list] = {}
        self.closed: list[str] = []
        self._autostart = autostart
        # Set by _make_rig in tests/conftest.py.
        self.inbox = None
        # Flipped on to make `recovery.rebuild` fail at its first step.
        self.rebuild_fails = False
        self.reconnected: list[str] = []
        # Every handle this manager was asked to BUILD, in order. A restart
        # replay that re-ran a task's payload, or that spawned onto a handle
        # the front end had already restored, shows up here and nowhere
        # else — both are silent against the roster alone.
        self.spawned: list[str] = []
        # handle -> every InboxMessage that reached that session.
        self._delivered: dict[str, list] = {}
        # handle -> live monitors it armed, read through `monitor_manager`.
        self._monitors: dict[str, list] = {}
        self.monitor_manager = StubMonitors(self._monitors)
        # Every session ever spawned, including closed ones. `close` drops a
        # session from the ROSTER, but the AgentSession object outlives it
        # with its observers still attached — which is how a late SystemInit
        # reaches the queue at all. The driving helpers resolve through
        # here; `get` stays roster-accurate, because `recovery.rebuild`
        # depends on it returning None for a handle that is gone.
        self._ever: dict[str, AgentSession] = {}

    def script(self, handle, events):
        self._scripts[handle] = events

    def spawn(self, slug, *, opening_prompt=None, handle=None, origin=None,
              resume_from=None, host=None, cwd=None, log_id=None):
        """``resume_from``/``log_id``/``host``/``cwd`` are what
        ``recovery.restore`` passes when it rebuilds a worker from its
        recorded Resumable. The real seam is
        ``SessionManager._sync_spawn``, which takes all four; the async
        ``spawn`` takes neither ``resume_from`` nor a return value a caller
        can observe, which is why both go through the sync one.

        A keyword missing here is not a signature nit: `restore` wraps the
        call, so a TypeError becomes None, becomes a park, and says nothing
        about why."""
        self.spawned.append(handle)
        self.resumed_from = resume_from
        self.spawned_log_id = log_id
        self.spawned_cwd = cwd
        # The real `_sync_spawn` stamps this onto the session; this stub
        # builds a bare AgentSession, so a caller's origin is only
        # observable if it is recorded here.
        self.spawned_origin = origin
        script = self._scripts.get(
            handle,
            [AssistantText(text="ok"),
             Result(duration_ms=1, is_error=False, usage=None)],
        )
        harness = HangingHarness() if script is HANG else FakeHarness(script)
        s = AgentSession(harness, None, slug, handle, project_root=Path.cwd())
        self._sessions.append(s)
        self._ever[handle] = s
        seen = self._delivered.setdefault(handle, [])
        s.add_inbox_observer(lambda _s, msg: seen.append(msg))
        if opening_prompt is not None and self._autostart:
            asyncio.create_task(s.send(opening_prompt))
        return s

    def preload_session(self, handle, *, session_id=None, slug="claude-impl",
                        script=None):
        """A worker tab the front end's ``plan_resume`` already restored.

        A queue worker is an ordinary entry in ``workspace.json`` with a
        ``session_id``, and ``plan_resume`` does not filter by origin, so
        by the time the queue replays its log the worker may already be
        standing. Built directly rather than through ``spawn`` so
        ``spawned`` keeps meaning "the replay built this".
        """
        harness = HangingHarness() if script is None else FakeHarness(script)
        harness.session_id = session_id
        s = AgentSession(harness, None, slug, handle, project_root=Path.cwd())
        self._sessions.append(s)
        self._ever[handle] = s
        seen = self._delivered.setdefault(handle, [])
        s.add_inbox_observer(lambda _s, msg: seen.append(msg))
        return s

    def get(self, handle):
        """``recovery.rebuild`` calls this after reconnect, to deliver the
        nudge onto the session it just rebuilt. Roster-accurate: None once
        the handle has been closed."""
        for s in self._sessions:
            if s.handle == handle:
                return s
        return None

    def list_sessions(self):
        """The roster plane, which `close_guard.gather_facts` reads first.

        It does not decide anything here. The two fields it fills that
        nothing else does — `exists` and `spawned_by` — are read only by
        `refuse_reasons`; `still_working_reasons` ignores both. And `state`
        is deliberately not the terminal state, because the finalizer passes
        that in itself: by the time it looks the roster may already disagree.
        The plane that makes the deferral reachable at all is
        `monitor_manager`, through `facts.monitors`.

        This is here because `gather_facts` once read `list_sessions` bare:
        a double without it raised AttributeError, `_still_working`
        swallowed that, and every worker read as "not waiting" — so a test
        claiming to prove the deferral takes precedence over the stall arm
        passed with the precedence check deleted. That read is defended now,
        so the double keeps this method to stay roster-accurate, not to
        unblock the assertion.
        """
        return [
            SimpleNamespace(
                handle=s.handle,
                agent_slug=getattr(s, "agent_slug", ""),
                state="ready",
                active=True,
                unseen=False,
                spawned_by=None,
            )
            for s in self._sessions
        ]

    def arm_monitor(self, handle):
        """Make this worker read as still waiting on something.

        A live monitor is the canonical reason a turn ending means nothing:
        the briefing tells an agent to end its turn and be woken.
        """
        self._monitors.setdefault(handle, []).append(
            SimpleNamespace(monitor_id="m1", handle=handle)
        )

    def _session_for(self, handle):
        s = self._ever.get(handle)
        if s is None:
            raise AssertionError(f"no stub session was ever spawned for {handle!r}")
        return s

    async def close(self, handle):
        self.closed.append(handle)
        self._sessions = [s for s in self._sessions if s.handle != handle]

    async def reconnect(self, handle, *, allow_local=False):
        """The seam ``recovery.rebuild`` goes through.

        Adopts a fresh HangingHarness, the way the real one adopts a fresh
        resumed process: everything aegis owns survives the swap, and the
        rebuilt worker is mid-task, so it does NOT end its turn on its own.
        Letting the original FakeHarness stand meant the nudge replayed its
        leftover script and the "recovered" worker completed cleanly four
        lines after the stall — every stall test then measured a
        completion.
        """
        if self.rebuild_fails:
            raise ValueError(f"{handle} has no session id to resume from")
        s = self.get(handle)
        if s is None:
            raise ValueError(f"unknown session {handle!r}")
        s.adopt(HangingHarness())
        self.reconnected.append(handle)
        return f"reconnected {handle} on local"

    # ----- driving a worker's turn by hand --------------------------------

    def emit_system_init(self, handle, session_id):
        """What a harness reports the moment it comes up. Synchronous: the
        queue's event observer runs inline, so the record is on disk by
        the time this returns."""
        s = self._session_for(handle)
        s._session.session_id = session_id
        s._fire_event(SystemInit(session_id=session_id))

    async def fail(self, handle, text="", *, stop_reason=None, error=None,
                   emit_twice=False):
        """End this worker's turn badly. ``emit_twice`` fires the state
        callback a second time, the way a harness that reports both an
        error and a stream end does.

        ``stop_reason`` and ``error`` are the two things the recovery plane
        reads to say WHY, and a bad turn end can carry either, both or
        neither — a stream that simply stops has no stop_reason and no
        exception. `_run_turn` sets the first from the Result and the second
        from its except clause, so a test that wants one drives it here.
        """
        await self._end_turn(handle, text, AgentState.error,
                            stop_reason=stop_reason, error=error,
                            emit_twice=emit_twice)

    async def finish(self, handle, text=""):
        """End this worker's turn cleanly."""
        await self._end_turn(handle, text, AgentState.ready)

    async def _end_turn(self, handle, text, state, *, stop_reason=None,
                        error=None, emit_twice=False):
        s = self._session_for(handle)
        if text:
            s._fire_event(AssistantText(text=text))
        s.last_stop_reason = stop_reason
        s.last_error = error
        s._emit_state(state, finished=True)
        if emit_twice:
            s._emit_state(state, finished=True)
        await _settle()

    def inbox_for(self, handle):
        """Every InboxMessage that reached this handle: one delivered onto
        a live worker session, or — for a producer with no session bound —
        whatever the router is holding for it."""
        if handle in self._ever:
            return list(self._delivered.get(handle, []))
        return list(self.inbox.pending(handle)) if self.inbox else []


async def _settle(cycles: int = 50):
    """Let the finalizer chain run. ``on_state`` schedules ``_finalize``
    as a task, which awaits the inbox and the close; none of that touches
    real I/O, so yielding the loop a bounded number of times is
    deterministic rather than a timeout in disguise."""
    for _ in range(cycles):
        await asyncio.sleep(0)


async def test_e2e_enqueue_to_callback_wakes_producer():
    inbox = InboxRouter()
    sm = StubSM()

    # Producer session, idle. Bound to the inbox so callbacks route to it.
    producer = AgentSession(
        FakeHarness(
            [AssistantText(text="thanks, will do"),
             Result(duration_ms=1, is_error=False, usage=None)],
        ),
        None, "producer", "lucid-knuth", inbox=inbox, project_root=Path.cwd())
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


async def test_restart_replays_handwritten_log_into_recoverable(tmp_path):
    """Deterministic isolation of the replay machinery: write a queue log
    by hand (enqueued + dispatched, no completion) and assert start()
    parks it with a callback in the producer's inbox.

    This used to assert `failed: interrupted`, and that WAS the loss this
    change removes: a task in flight at the crash was declared lost while
    its worker's tab came back from `plan_resume` with the whole
    conversation in it. The log here has no `worker_session` record, so
    there is genuinely no conversation id to resume from and the task
    parks rather than resuming — but it is parked and still reachable,
    not failed.
    """
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
    assert st["status"] == "recoverable"
    assert "no conversation to resume" in (st["error"] or "")

    # Callback persisted to the producer's inbox file, so a producer that
    # was not running when the queue replayed still learns what happened.
    inbox_log = read_records(tmp_path / "inboxes" / "lucid-knuth.jsonl")
    assert any(
        r.get("task_id") == "01TID1" and r.get("status") == "error"
        for r in inbox_log)

    # The replay also appended a "recoverable" record to the queue log, so
    # the next restart reads the task as parked and says nothing again.
    qlog = read_records(qfile)
    assert qlog[-1]["event"] == "recoverable"
    assert qlog[-1]["task_id"] == "01TID1"


async def test_restart_round_trip_crashes_in_flight_and_recovers(tmp_path):
    """Full round-trip: enqueue → dispatch (worker hangs) → 'crash' →
    fresh QueueManager.start() against the same state_dir reads the log
    and parks the task, with a callback in the inbox file.

    The hanging worker never reports a SystemInit, so no `worker_session`
    record reaches the log and there is no conversation id to resume
    from. Parked, not failed: the difference is whether the operator can
    still act on it."""
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
    assert st["status"] == "recoverable"
    assert "no conversation to resume" in (st["error"] or "")

    inbox_log = read_records(tmp_path / "inboxes" / "lucid-knuth.jsonl")
    assert any(r.get("task_id") == tid and r.get("status") == "error"
               for r in inbox_log)
