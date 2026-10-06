"""A rebuild must not leave the dead turn's epilogue running.

`close` cancels and awaits `_task`; `adopt` does not. On the
`Result(is_error=True)` path the queue's finalizer is scheduled from
inside the stream loop and actually runs from the `digest.build` await,
so the OLD turn is parked mid-epilogue while its harness is replaced
underneath it. Left alone it resumes and runs to `_chain_if_pending`,
which starts a SECOND turn on the freshly-adopted harness and overwrites
`self._task`. Two turns then interleave on one session, and whichever
ends first hands the queue its state — a `ready` there takes the
completion path and CLOSES a worker that is still mid-task, which is the
exact destruction this whole recovery plane exists to prevent.

The tier used here is the reminder (`add_reminder`), deliberately: the
inbox tier is drained by the nudge's own `deliver`, so only a reminder,
a pending harness event or an armed loop can still be waiting at the
moment the old epilogue resumes.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.core.recovery import rebuild
from aegis.digest.models import TurnFacts
from aegis.events import Result
from aegis.hosts.models import HostSpec
from aegis.queue.schema import InboxMessage, now_iso, sender_substrate
from aegis.tui.state import AgentState

NUDGE = "Your aegis session was interrupted mid-task. Continue."


class ScriptedHarness:
    """Emits a fixed event script, then either ends the stream or hangs.

    `before` runs inside `events()`, which is inside the turn — the only
    place a test can leave a reminder the way an agent does, since
    `add_reminder` on an idle session chains immediately.
    """

    supports_idle_events = False

    def __init__(self, *, session_id, script=(), hang=False):
        self._session_id = session_id
        self._script = list(script)
        self._hang = hang
        self.before = None
        self.sent: list[str] = []
        self.closed = False

    @property
    def session_id(self):
        return self._session_id

    async def start(self):
        pass

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        if self.before is not None:
            self.before()
        for ev in self._script:
            yield ev
        if self._hang:
            await asyncio.Event().wait()

    async def close(self):
        self.closed = True

    def has_pending_event(self) -> bool:
        return False


async def _settle(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    return predicate()


@pytest.fixture
async def stalling_worker(tmp_path):
    """A live session whose first turn ends in `Result(is_error=True)`,
    with its epilogue held open at `digest.build` and a reminder pending.

    Yields `(mgr, handle, built, gate, old_task_box)`.
    """
    built: list[ScriptedHarness] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        first = not built
        h = ScriptedHarness(
            session_id="sid-1",
            # Turn 1 ends badly and the stream ends, so the epilogue runs.
            # Every harness after the rebuild hangs, so its turn stays in
            # flight and a second `send` can only come from chaining.
            script=[Result(duration_ms=1, is_error=True)] if first else [],
            hang=not first,
        )
        built.append(h)
        return h

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        roots=AegisRoots.for_project(tmp_path),
    )
    handle = await mgr.spawn("main")
    s = mgr.get(handle)
    assert s.place.is_local

    # Park the epilogue exactly where the queue's finalizer really runs.
    gate = asyncio.Event()

    async def held_build(**_kw):
        await gate.wait()
        return TurnFacts()

    s.digest.build = held_build

    # The agent leaves itself a reminder mid-turn, as `aegis_remind` does.
    built[0].before = lambda: s.add_reminder(
        InboxMessage(sender=sender_substrate(), timestamp=now_iso(),
                     body="the reminder I left myself"))

    # Stand in for `QueueManager._on_state`: schedule the rebuild as its
    # own task off the finished-state callback, which is what makes the
    # old turn and the rebuild concurrent in the first place.
    rebuilt = asyncio.Event()

    async def _recover():
        await rebuild(mgr, handle, nudge=NUDGE)
        rebuilt.set()

    def on_state(_s, st, finished):
        if finished and st is AgentState.error:
            asyncio.create_task(_recover())

    s.add_state_observer(on_state)

    await s.deliver(InboxMessage(sender=sender_substrate(),
                                 timestamp=now_iso(), body="do the task"))
    old_task = s._task
    assert await _settle(rebuilt.is_set), "the rebuild never ran"
    assert len(built) == 2, "the rebuild must build exactly one new harness"
    yield mgr, handle, built, gate, old_task
    gate.set()


async def test_the_old_turn_does_not_chain_onto_the_rebuilt_harness(
    stalling_worker,
):
    mgr, handle, built, gate, old_task = stalling_worker
    s = mgr.get(handle)
    rebuilt_harness = built[1]

    assert await _settle(lambda: len(rebuilt_harness.sent) == 1), (
        "the nudge turn should be the rebuilt harness's first and only turn")
    assert NUDGE in rebuilt_harness.sent[0]
    nudge_task = s._task

    # Release the dead turn's epilogue. It must not reach _chain_if_pending.
    gate.set()
    await _settle(old_task.done)
    await asyncio.sleep(0.05)

    assert len(rebuilt_harness.sent) == 1, (
        f"the dead turn chained a second turn onto the rebuilt harness: "
        f"{rebuilt_harness.sent!r}")
    assert s._task is nudge_task, (
        "the dead turn's chain overwrote _task, so the nudge turn is now "
        "unawaited and whichever turn ends first speaks for the session")


async def test_the_reminder_survives_for_the_live_turn(stalling_worker):
    """Cancelling the dead turn must not eat what it was holding. The
    reminder is not delivered by the rebuild, it just stays pending — the
    nudge turn's own boundary drains it, one turn at a time."""
    mgr, handle, built, gate, old_task = stalling_worker
    s = mgr.get(handle)

    gate.set()
    await _settle(old_task.done)
    await asyncio.sleep(0.05)

    assert [m.body for m in s._reminders] == ["the reminder I left myself"]
