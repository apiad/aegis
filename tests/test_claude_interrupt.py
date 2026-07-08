"""Interrupt must actually stop the claude subprocess.

Root cause (reproduced): AgentSession.interrupt() cancelled the local read
task but never signalled the subprocess, so `claude -p` kept running the
turn to completion (burning tokens, running tools) and its terminal events
bled into the next turn's queue. The fix sends a stream-json
`control_request/interrupt` — which the CLI honours by aborting the turn and
emitting a terminal error result, keeping the process alive — then drains
those terminal events so the next turn starts clean.
"""
from __future__ import annotations

import asyncio
import json
import types

from aegis.drivers.claude import ClaudeSession
from aegis.events import AssistantText, Result


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, b: bytes) -> None:
        self.written.append(b)

    async def drain(self) -> None:
        pass


def _fake_proc() -> types.SimpleNamespace:
    return types.SimpleNamespace(stdin=_FakeStdin(), returncode=None)


def test_interrupt_sends_control_request():
    async def scenario():
        sess = ClaudeSession(["claude"], "/tmp")
        sess._proc = _fake_proc()
        # A terminal result is what the CLI emits after an interrupt.
        sess._queue.put_nowait(Result(duration_ms=1, is_error=True))
        await sess.interrupt()
        return sess._proc.stdin.written

    written = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert written, "interrupt must write a control_request to stdin"
    msg = json.loads(written[-1].decode())
    assert msg["type"] == "control_request"
    assert msg["request"]["subtype"] == "interrupt"
    assert msg["request_id"]  # non-empty, unique per call


def test_interrupt_drains_terminal_events():
    """Events the CLI emits for the aborted turn must not linger in the
    queue where the next turn's events() loop would consume them."""
    async def scenario():
        sess = ClaudeSession(["claude"], "/tmp")
        sess._proc = _fake_proc()
        sess._queue.put_nowait(AssistantText(text="partial"))
        sess._queue.put_nowait(Result(duration_ms=1, is_error=True))
        await sess.interrupt()
        return sess._queue.empty()

    drained = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert drained, "interrupt must drain events up to and including the result"


def test_interrupt_noop_without_live_proc():
    """No subprocess (or already exited) -> interrupt is a harmless no-op."""
    async def scenario():
        sess = ClaudeSession(["claude"], "/tmp")
        await sess.interrupt()  # _proc is None
        sess._proc = types.SimpleNamespace(stdin=_FakeStdin(), returncode=0)
        await sess.interrupt()  # already exited
        return sess._proc.stdin.written

    written = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert written == []


def test_interrupt_unique_request_ids():
    async def scenario():
        sess = ClaudeSession(["claude"], "/tmp")
        sess._proc = _fake_proc()
        sess._queue.put_nowait(Result(duration_ms=1, is_error=True))
        await sess.interrupt()
        sess._queue.put_nowait(Result(duration_ms=1, is_error=True))
        await sess.interrupt()
        ids = [json.loads(b.decode())["request_id"]
               for b in sess._proc.stdin.written]
        return ids

    ids = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert len(ids) == 2 and ids[0] != ids[1]
