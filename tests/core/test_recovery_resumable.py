"""Without a session id there is nothing to resume, so the record that
carries it is the whole of what makes recovery possible.

Two kinds of test live here. The pure ones drive `resumable_from` with
namespace doubles, because it must survive every stand-in a caller hands
it. The rest drive a real `AgentSession`: a double agrees with any
attribute name at all, so only a real session can catch `agent_slug`
being renamed out from under the probe.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from aegis.config import Agent
from aegis.core.recovery import Resumable, resumable_from
from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result


def _session(session_id, *, slug="impl", harness="claude-code",
             cwd="/srv/app", host="local"):
    return SimpleNamespace(
        session_id=session_id,
        agent_slug=slug,
        agent=SimpleNamespace(harness=harness),
        place=SimpleNamespace(cwd=cwd, host=host),
    )


def test_returns_none_before_the_harness_reports_an_id():
    assert resumable_from(_session(None)) is None


def test_returns_none_for_an_empty_id():
    """An empty string is not an id; resuming on it would build a new
    conversation and call it a recovery."""
    assert resumable_from(_session("")) is None


def test_captures_everything_the_rebuild_needs():
    r = resumable_from(_session("sess-abc"))
    assert r == Resumable(
        session_id="sess-abc",
        agent_profile="impl",
        provider="claude-code",
        cwd="/srv/app",
        host="local",
    )


def test_missing_place_falls_back_to_local():
    """A session built before placement existed, and every test double."""
    s = _session("sess-abc")
    s.place = None
    r = resumable_from(s)
    assert r is not None and r.host == "local" and r.cwd == ""


def test_survives_a_double_carrying_none_of_the_fields():
    """The probe runs while a task is being saved. Raising here strands
    the very work it was called to rescue."""
    assert resumable_from(SimpleNamespace()) is None


# --------------------------------------------------------------------------
# Against a real AgentSession. The doubles above would pass unchanged if
# `agent_slug` or `place` were renamed tomorrow; these would not.
# --------------------------------------------------------------------------


class FakeSession:
    """The repo's usual harness double (see tests/test_core_session.py),
    plus the `session_id` a real driver latches on its first SystemInit."""

    def __init__(self, events, session_id=None):
        self._events = list(events)
        self.session_id = session_id
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
        for e in self._events:
            await asyncio.sleep(0)
            yield e


class WakeableFakeSession(FakeSession):
    """A harness that emits events after its own Result, the way Claude
    does when a Monitor fires. Drives `_drain_unsolicited_turn`, which is
    the session's second live turn loop. Copied in shape from
    tests/test_core_session.py."""

    supports_idle_events = True

    def has_pending_event(self) -> bool:
        return bool(self._events)

    def feed(self, *events):
        self._events.extend(events)

    async def events(self):
        while self._events:
            await asyncio.sleep(0)
            ev = self._events.pop(0)
            yield ev
            if isinstance(ev, Result):
                return


def _agent_session(harness):
    # A real Agent, not None: `provider` is read off `Agent.harness`, and
    # it is the one field of four that no other real-session test pins. With
    # agent=None, renaming `Agent.harness` reddened only the namespace
    # doubles above, which agree with any attribute name at all.
    return AgentSession(harness, agent=Agent(harness="claude-code"),
                        agent_slug="default", handle="h1",
                        project_root=Path.cwd())


def test_reads_the_field_names_a_real_session_actually_has():
    """`session_id`, `agent_slug` and `place` are read off a live
    AgentSession by name. A rename that a namespace double would shrug
    off breaks recovery in production."""
    s = _agent_session(FakeSession([], session_id="sess-real"))
    r = resumable_from(s)
    assert r is not None
    assert r.session_id == "sess-real"
    assert r.agent_profile == "default"
    assert r.provider == "claude-code"
    assert r.host == "local"
    assert r.cwd == str(Path.cwd())


def test_a_real_session_with_no_harness_id_is_not_resumable():
    """`AgentSession.session_id` delegates to the driver, which reports
    None until its first SystemInit."""
    assert resumable_from(_agent_session(FakeSession([]))) is None


# --------------------------------------------------------------------------
# last_stop_reason. Diagnostic only: the queue writes it into the stall
# log for a human to read, and nothing branches on it.
# --------------------------------------------------------------------------


async def test_last_stop_reason_is_none_on_a_fresh_session():
    """The queue reads this in the stall log. An AttributeError there
    loses the diagnostic for the very failure it is recording."""
    s = _agent_session(FakeSession([]))
    assert s.last_stop_reason is None


async def test_last_stop_reason_latches_from_the_result():
    s = _agent_session(FakeSession([
        Result(duration_ms=None, is_error=True, stop_reason="link_lost"),
    ]))
    await s.send("go")
    await s._task
    assert s.last_stop_reason == "link_lost"


async def test_last_stop_reason_latches_in_the_unsolicited_drain():
    """The session has two live turn loops: `_run_turn`, and
    `_drain_unsolicited_turn` for events a harness emits with no prompt
    behind them. Recording the reason on one path and not the other is
    worse than recording neither, because the log then looks complete.
    """
    s = _agent_session(WakeableFakeSession([
        AssistantText(text="first"),
        Result(duration_ms=1, is_error=False, stop_reason="end_turn"),
    ]))
    # The poll loop below is a budget in wall-clock; the drain's own cadence
    # is `_idle_poll_seconds`, 0.25 by default, which leaves four cycles on a
    # box under load. Shrink the cadence rather than widening the budget.
    s._idle_poll_seconds = 0.01
    s._session.feed(
        AssistantText(text="monitor-wake"),
        Result(duration_ms=1, is_error=True, stop_reason="link_lost"),
    )
    await s.send("hello")
    await s._task
    for _ in range(100):
        if s.last_stop_reason == "link_lost":
            break
        await asyncio.sleep(0.01)
    assert s.last_stop_reason == "link_lost", (
        "the unsolicited drain must latch the reason too")


class PerTurnFakeSession(FakeSession):
    """A harness that hands out a different batch of events per turn, so a
    test can run two turns against one session. `FakeSession` replays the
    same list every time `events()` is called, which cannot express "the
    second turn died mid-stream"."""

    def __init__(self, turns, session_id=None):
        super().__init__([], session_id=session_id)
        self._turns = [list(t) for t in turns]

    async def events(self):
        batch = self._turns.pop(0) if self._turns else []
        for e in batch:
            await asyncio.sleep(0)
            yield e


async def test_last_stop_reason_is_cleared_at_the_start_of_each_turn():
    """A turn that dies with no Result at all is precisely the case the
    recovery plane exists for. If the field still holds the PREVIOUS
    turn's reason, the stall log records it as this failure's cause — a
    stale reason reads as evidence, which is worse than an empty one."""
    s = _agent_session(PerTurnFakeSession([
        [Result(duration_ms=1, is_error=True, stop_reason="link_lost")],
        [AssistantText(text="stream died here")],  # no Result at all
    ]))

    await s.send("one")
    await s._task
    assert s.last_stop_reason == "link_lost"

    await s.send("two")
    await s._task
    assert s.last_stop_reason is None, (
        "the second turn produced no Result, so its stop reason is "
        "unknown, not the first turn's"
    )


async def test_last_stop_reason_is_cleared_in_the_unsolicited_drain_too():
    """Both live loops clear, for the same reason both latch: recording a
    stale reason on one path and not the other makes the log look
    complete when it is not."""
    s = _agent_session(WakeableFakeSession([
        AssistantText(text="first"),
        Result(duration_ms=1, is_error=True, stop_reason="link_lost"),
    ]))
    s._idle_poll_seconds = 0.01  # same load-flakiness as the test above
    s._session.feed(AssistantText(text="monitor-wake"))  # drain, no Result
    await s.send("hello")
    await s._task

    # No mid-flight assertion that the field still reads "link_lost": the
    # drain starts before `_task` resolves, so that would race its clear.
    # Draining the fed event is the signal the second loop actually ran,
    # and without it the field would still hold turn one's reason.
    for _ in range(200):
        if not s._session.has_pending_event():
            break
        await asyncio.sleep(0.01)
    assert not s._session.has_pending_event(), "the drain never ran"
    assert s.last_stop_reason is None, (
        "the drain consumed a turn that produced no Result"
    )
