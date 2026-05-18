"""Regression: the stdout pump must never silent-hang the turn.

Root cause (reproduced): a tool_result line larger than asyncio's 64 KiB
StreamReader limit (e.g. the agent reading SOUL.md) raised inside
_pump_stdout, which had no try/finally, so the 'stream closed' sentinel
never fired and events() blocked on an empty queue forever.
"""
from __future__ import annotations

import asyncio
import types

from aegis.drivers import claude
from aegis.drivers.claude import _STREAM_LIMIT, ClaudeSession


def test_pump_always_delivers_sentinel_on_loop_exception(monkeypatch):
    """If the pump loop raises, the None sentinel is still delivered so
    events() returns instead of deadlocking."""
    monkeypatch.setattr(
        claude, "parse",
        lambda _line: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"type":"x"}\n')
        reader.feed_eof()
        sess = ClaudeSession(["claude"], "/tmp")
        sess._proc = types.SimpleNamespace(stdout=reader)
        await sess._pump_stdout()  # must not raise, must finish
        # events() must terminate (sees the sentinel), not hang.
        out = [ev async for ev in sess.events()]
        return out

    result = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert result == []  # parse raised → no events, but a clean end


def test_start_requests_stream_limit_above_64kib(monkeypatch):
    """start() must hand create_subprocess_exec a limit far above the
    64 KiB default that caused the SOUL.md hang."""
    captured = {}

    async def fake_exec(*_args, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            stdout=asyncio.StreamReader(), returncode=0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def scenario():
        sess = ClaudeSession(["claude"], "/tmp")
        await sess.start()
        if sess._reader:
            sess._reader.cancel()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert captured["limit"] == _STREAM_LIMIT
    assert captured["limit"] > 64 * 1024
