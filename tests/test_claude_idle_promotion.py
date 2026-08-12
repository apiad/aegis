"""Regression: an out-of-band system notice must not strand the session.

Root cause (reproduced from ``plush-pearl``'s log, 2026-08-12): after a
turn's ``Result``, claude spontaneously emitted
``{"type":"system","subtype":"commands_changed"}`` — a notice it sends when
skills/commands reload. It belongs to no turn and is never followed by a
``Result``.

``has_pending_event()`` only asked whether the queue was non-empty, so the
idle watcher promoted that lone notice into an unsolicited turn. The drain
then blocked forever on the next queue read, waiting on a ``Result`` that
was never coming, and the session sat in ``working`` — rendering as a
session that thinks forever without an event ever arriving.

The whole family shows up in the logs the same way: a tail of pure system
notices (``commands_changed``, ``init``, ``hook_started``, ``task_updated``,
``task_notification``) after the final ``Result``.

These tests use the REAL ``ClaudeSession`` queue on purpose. The fake in
``test_core_session.py`` drains its list and returns, so it cannot block —
it structurally cannot reproduce this hang.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aegis.core.session import AgentSession
from aegis.drivers.claude import ClaudeSession
from aegis.tui.state import AgentState

RESULT = ('{"type":"result","subtype":"success","is_error":false,'
          '"duration_ms":1,"usage":{}}')
# The exact out-of-band notices observed stranding real sessions.
COMMANDS_CHANGED = ('{"type":"system","subtype":"commands_changed",'
                    '"commands":[{"name":"saidkick","description":"x"}]}')
INIT = '{"type":"system","subtype":"init","session_id":"s1","model":"opus"}'
HOOK_STARTED = '{"type":"system","subtype":"hook_started","hook":"x"}'
TASK_UPDATED = '{"type":"system","subtype":"task_updated","task_id":"t1"}'
ASSISTANT = ('{"type":"assistant","message":{"id":"m1","role":"assistant",'
             '"content":[{"type":"text","text":"unprompted"}]}}')


def _wired() -> tuple[ClaudeSession, asyncio.StreamReader, asyncio.Task]:
    """A ClaudeSession reading a StreamReader we feed by hand."""
    reader = asyncio.StreamReader()
    sess = ClaudeSession(["claude"], "/tmp")
    sess._proc = types.SimpleNamespace(stdout=reader)
    pump = asyncio.create_task(sess._pump_stdout())
    return sess, reader, pump


async def _feed(reader: asyncio.StreamReader, *lines: str) -> None:
    for line in lines:
        reader.feed_data((line + "\n").encode())
    await asyncio.sleep(0.05)  # let the pump parse and enqueue


@pytest.mark.asyncio
@pytest.mark.parametrize("notice", [
    COMMANDS_CHANGED, INIT, HOOK_STARTED, TASK_UPDATED,
])
async def test_out_of_band_notice_is_not_pending(notice):
    """A system notice arriving after a Result must not read as a
    turn waiting to be drained."""
    sess, reader, pump = _wired()
    try:
        await _feed(reader, RESULT)
        assert [type(e).__name__ async for e in sess.events()] == ["Result"]

        await _feed(reader, notice)
        assert sess.has_pending_event() is False, (
            "an out-of-band system notice must not be promoted to a turn")
    finally:
        pump.cancel()


@pytest.mark.asyncio
async def test_real_unprompted_turn_is_still_pending():
    """The feature this guard protects must survive: genuine unprompted
    assistant output (a background task speaking up) still promotes."""
    sess, reader, pump = _wired()
    try:
        await _feed(reader, RESULT)
        [_ async for _ in sess.events()]

        # The notice alone is not a turn...
        await _feed(reader, COMMANDS_CHANGED)
        assert sess.has_pending_event() is False
        # ...but assistant output behind it is.
        await _feed(reader, ASSISTANT)
        assert sess.has_pending_event() is True

        # And the queued notice is not lost — it drains with that turn.
        await _feed(reader, RESULT)
        kinds = [type(e).__name__ async for e in sess.events()]
        assert kinds == ["Unknown", "AssistantText", "Result"], kinds
    finally:
        pump.cancel()


@pytest.mark.asyncio
async def test_stream_end_still_promotes():
    """A dead harness must still wake the session so it can go to error —
    the end-of-stream sentinel is not an out-of-band notice."""
    sess, reader, pump = _wired()
    try:
        await _feed(reader, RESULT)
        [_ async for _ in sess.events()]

        reader.feed_eof()
        await asyncio.sleep(0.05)
        assert sess.has_pending_event() is True
    finally:
        pump.cancel()


@pytest.mark.asyncio
async def test_session_settles_idle_after_out_of_band_notice():
    """The structural test: the whole session, not just the predicate.

    After the turn ends, a spontaneous notice arrives while idle. The
    session must settle back to ``ready``. Before the fix it pinned at
    ``working`` forever — thinking with no event ever coming back.
    """
    sess, reader, pump = _wired()
    s = AgentSession(sess, agent=None, agent_slug="default", handle="h1")
    s._idle_poll_seconds = 0.01
    try:
        turn = asyncio.create_task(s.send("hello"))
        await _feed(reader, RESULT)
        await turn
        await s._task
        await asyncio.sleep(0.05)
        assert s.state is AgentState.ready

        # Claude reloads its skills 67s later and says so, out of band.
        await _feed(reader, COMMANDS_CHANGED, INIT)

        # Give the idle watcher many poll intervals to misbehave.
        await asyncio.sleep(0.3)
        assert s.state is AgentState.ready, (
            "an out-of-band notice stranded the session in `working`")
    finally:
        await s.interrupt()
        pump.cancel()
