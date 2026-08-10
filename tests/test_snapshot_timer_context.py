"""The debounced roster snapshot must survive a context-less caller.

Textual's ``Timer`` task copies whatever context created it, and ``_tick``
reads the ``active_app`` ContextVar. Every AppBridge method reachable from
an MCP tool handler (``aegis_title``, ``aegis_rename``, spawn, close, …)
runs in a task that has no ``active_app`` — so a timer armed from there dies
on its first tick with ``LookupError``. Nothing retrieves that exception
until shutdown, when ``Timer._stop_all`` awaits the task and re-raises it
inside ``App._process_messages``: the crash dump Ctrl+Q printed every time.
"""
from __future__ import annotations

import asyncio
import contextvars

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(agent, mcp_url, handle):
    return FakeSession()


@pytest.mark.asyncio
async def test_snapshot_timer_armed_without_active_app_still_ticks(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AegisApp, "SNAPSHOT_DEBOUNCE_S", 0.05)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await asyncio.sleep(0.2)        # let any boot-armed debounce flush
        assert app._snapshot_timer is None

        # A fresh Context is what an MCP handler task hands us: no active_app.
        contextvars.Context().run(app._schedule_snapshot)
        timer = app._snapshot_timer
        assert timer is not None, "expected a debounce timer to be armed"

        await asyncio.sleep(0.3)

        task = timer._task
        assert task is not None and task.done()
        # With the bug this is LookupError(active_app) — a dead task that
        # Textual re-raises out of Timer._stop_all at quit.
        assert task.exception() is None
        # …and because _flush_snapshot never ran, the debounce stayed armed
        # forever, silently disabling every later roster write.
        assert app._snapshot_timer is None
