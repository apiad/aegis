"""Scanning the session logs must not run on the event loop.

`list_history` reads and decodes every log in the state dir. Against a real
corpus (232 logs, 615 MB) that measured 25s warm / 60s cold — and it ran on
the event loop, so the whole TUI was frozen for the duration. It belongs on
a thread; the modal that follows it stays on the loop.
"""
from __future__ import annotations

import threading

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("x")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


@pytest.mark.asyncio
async def test_history_scan_runs_off_the_event_loop(monkeypatch):
    app = AegisApp({"default": _agent()}, "default",
                   lambda *a, **kw: FakeSession(), FakeMCP())
    seen: dict = {}

    def fake_list_history(*a, **kw):
        seen["thread"] = threading.get_ident()
        return []

    monkeypatch.setattr("aegis.state.history.list_history", fake_list_history)

    async with app.run_test() as pilot:
        seen["loop_thread"] = threading.get_ident()
        app.action_open_history()
        for _ in range(50):
            await pilot.pause()
            if "thread" in seen:
                break
        # Close the modal the action opens so teardown is clean.
        with __import__("contextlib").suppress(Exception):
            app.pop_screen()

    assert "thread" in seen, "list_history was never called"
    assert seen["thread"] != seen["loop_thread"], (
        "list_history ran on the event loop — the UI freezes for the "
        "duration of the scan")
