"""/loop — a self-re-arming turn-end instruction.

Layers, mirroring tests/test_reminder.py:
- the loop tier on AgentSession (lowest rung of _chain_if_pending)
- LoopService (handle -> session routing)
- the aegis_loop_stop MCP tool
- the /loop slash command
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


# --------------------------------------------------------------------------
# Fakes (same shapes as tests/test_reminder.py)
# --------------------------------------------------------------------------
class FakeHarness:
    def __init__(self, events_per_turn=None):
        self._turns = list(events_per_turn or [])
        self.started = False
        self.closed = False
        self.sent: list[str] = []
        self.session_id = None

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        evs = self._turns.pop(0) if self._turns else []
        for e in evs:
            await asyncio.sleep(0)
            yield e


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        pass

    async def stop(self):
        pass


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _turn(text):
    return [AssistantText(text=text),
            Result(duration_ms=1, is_error=False, usage=None)]


def _factory(agent, mcp_url, handle):
    return FakeHarness()


# --------------------------------------------------------------------------
# Task 1 — bridge session lookup
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_app_get_returns_agent_session(tmp_path, monkeypatch):
    """AegisApp.get(handle) -> AgentSession. Without it the TUI's
    ReminderService (and LoopService) silently find no session and every
    turn-end delivery errors out."""
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        session = app.get(handle)
        assert isinstance(session, AgentSession)
        assert session.handle == handle
        assert app.get("no-such-handle") is None
