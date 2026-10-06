"""A rebuilt harness has no subprocess until someone starts it.

`adopt` swaps the harness under a live session, and `_run_turn` calls
`start()` only when `_started` is False. Left set from the dead process's
life, the next turn sends to a driver that never spawned — `ClaudeSession.send`
asserts on `self._proc` — so the turn dies before a byte reaches the model
while `recovery.rebuild` reports success.

Against a real claude on 2026-09-25 that read as a second bad turn end
four milliseconds after the first: one SIGKILL spent the whole retry
budget, and the task the plane had just recovered was parked with
`attempts=2` having never been resumed. No stub caught it, because every
stub harness in the suite starts lazily and tolerates a `send` with no
process behind it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.core.session import AgentSession
from aegis.events import AssistantText, RecapNote, Result
from aegis.tui.state import AgentState


class StrictHarness:
    """Holds the drivers' own contract: `send` before `start` is a bug."""

    def __init__(self, events=(), session_id="sid"):
        self._events = list(events)
        self.session_id = session_id
        self.started = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def send(self, text):
        if not self.started:
            raise AssertionError("send() before start(): no process to write to")
        self.sent.append(text)

    async def events(self):
        for e in self._events:
            await asyncio.sleep(0)
            yield e

    async def close(self):
        pass


def _done(text):
    return [AssistantText(text=text), Result(duration_ms=1, is_error=False, usage=None)]


@pytest.mark.asyncio
async def test_a_turn_after_adopt_starts_the_new_harness(tmp_path: Path):
    first = StrictHarness(_done("1"))
    s = AgentSession(first, None, "default", "h1", project_root=tmp_path)
    await s.send("count")
    await s._task
    assert first.started

    second = StrictHarness(_done("RESUMING AT 2"), session_id="sid-2")
    s.adopt(second)
    await s.send("continue from where you were")
    await s._task

    assert second.started, "adopt left _started set; the rebuilt harness never spawned"
    assert second.sent == ["continue from where you were"]
    assert s.state is AgentState.ready


@pytest.mark.asyncio
async def test_adopt_does_not_reopen_the_card_rehydration_window(tmp_path: Path):
    """`rehydrate_card` refuses a session that has run a turn in this
    process, and `_started` is how it knows. A rebuilt session has run
    turns — its metrics and transcript survived the swap — so replaying the
    log into it would add the log's totals on top of the live ones."""
    s = AgentSession(StrictHarness(_done("1")), None, "default", "h1",
                     project_root=tmp_path)
    await s.send("count")
    await s._task
    s.adopt(StrictHarness(_done("2"), session_id="sid-2"))

    s.rehydrate_card([RecapNote(line="a line off the log")], [])

    assert s._last_recap_line == ""
