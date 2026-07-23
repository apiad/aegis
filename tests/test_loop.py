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


# --------------------------------------------------------------------------
# Task 2 — the loop tier on AgentSession
# --------------------------------------------------------------------------
from aegis.core.loop import LoopState          # noqa: E402
from aegis.queue import InboxMessage, sender_loop   # noqa: E402
from aegis.tui.state import AgentState          # noqa: E402


def _inbox(body):
    return InboxMessage(sender="queue:impl", timestamp="2026-07-23T00:00:00Z",
                        body=body, task_id="01J", status="ok")


def _remind(body):
    from aegis.queue import sender_reminder
    return InboxMessage(sender=sender_reminder(),
                        timestamp="2026-07-23T00:00:00Z", body=body)


async def _settle(session):
    """Let every chained turn run to completion."""
    for _ in range(50):
        await asyncio.sleep(0)
        if session.state is not AgentState.working:
            return


def test_sender_loop_renders_iteration():
    assert sender_loop(3, 20) == "loop · iteration 3/20"


def test_loop_state_render_includes_text_and_stop_tool():
    ls = LoopState(text="fix the tests")
    body = ls.render("witty-wirth")
    assert "fix the tests" in body
    assert "aegis_loop_stop" in body
    assert "witty-wirth" in body


@pytest.mark.asyncio
async def test_loop_refires_at_turn_end_and_counts():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=3)
    await _settle(s)
    # Armed while idle -> promoted immediately, then re-fires to the cap.
    assert len(harness.sent) == 3
    assert all("keep going" in t for t in harness.sent)
    assert s.loop_status() is None          # cleared by the cap


@pytest.mark.asyncio
async def test_loop_header_carries_iteration():
    harness = FakeHarness([_turn("a")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=1)
    await _settle(s)
    assert "> from loop · iteration 1/1" in harness.sent[0]


@pytest.mark.asyncio
async def test_inbox_message_preempts_the_loop():
    """Tier order: a buffered inbox message dispatches before the loop."""
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("LOOPTEXT", max_iterations=2)
    await asyncio.sleep(0)          # first loop turn is in flight
    await s.deliver(_inbox("INBOXTEXT"))
    await _settle(s)
    order = ["INBOX" if "INBOXTEXT" in t else "LOOP" for t in harness.sent]
    assert order[0] == "LOOP"       # armed-while-idle fired first
    assert order[1] == "INBOX"      # then the inbox, ahead of the next loop
    assert order[2] == "LOOP"


@pytest.mark.asyncio
async def test_reminder_preempts_the_loop():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("LOOPTEXT", max_iterations=2)
    await asyncio.sleep(0)
    s.add_reminder(_remind("REMINDTEXT"))
    await _settle(s)
    assert "REMINDTEXT" in harness.sent[1]


@pytest.mark.asyncio
async def test_stop_loop_prevents_the_next_iteration():
    harness = FakeHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=10)
    await asyncio.sleep(0)
    assert s.stop_loop("agent says done") is True
    await _settle(s)
    assert len(harness.sent) == 1
    assert s.loop_status() is None
    assert s.stop_loop("again") is False      # idempotent


@pytest.mark.asyncio
async def test_loop_status_reports_progress():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=5)
    await asyncio.sleep(0)
    st = s.loop_status()
    assert st["text"] == "keep going"
    assert st["max_iterations"] == 5
    assert st["iteration"] >= 1
    s.stop_loop()


@pytest.mark.asyncio
async def test_arming_twice_replaces():
    harness = FakeHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("first", max_iterations=10)
    await asyncio.sleep(0)
    s.arm_loop("second", max_iterations=10)
    st = s.loop_status()
    assert st["text"] == "second"
    assert st["iteration"] == 0
    s.stop_loop()


@pytest.mark.asyncio
async def test_cap_fires_the_observer_with_a_capped_reason():
    seen = []
    harness = FakeHarness([_turn("a")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.on_loop = lambda sess, state, reason: seen.append((state, reason))
    s.arm_loop("keep going", max_iterations=1)
    await _settle(s)
    reasons = [r for _, r in seen]
    assert any("capped" in r for r in reasons)
