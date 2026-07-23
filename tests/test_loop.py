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


# --------------------------------------------------------------------------
# Task 3 — termination edges
# --------------------------------------------------------------------------
class HoldHarness(FakeHarness):
    """Never reports pending events; used with _unsolicited_hold set by hand."""


@pytest.mark.asyncio
async def test_loop_yields_to_an_armed_monitor():
    """_unsolicited_hold > 0 means an aegis monitor is the authoritative
    waker. Firing underneath it turns `/loop run the tests` into a spin loop:
    the agent would burn whole turns asking 'done yet?' while the monitor sits
    there waiting to wake it."""
    harness = HoldHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s._unsolicited_hold = 1
    s.arm_loop("keep going", max_iterations=5)
    await _settle(s)
    assert harness.sent == []                       # suppressed
    assert s.loop_status()["iteration"] == 0        # counter did not advance
    # Releasing the hold lets it fire (and then run on to its cap, which is
    # the point — the suppression was the hold, not the loop being spent).
    s._unsolicited_hold = 0
    s._chain_if_pending()
    await _settle(s)
    assert len(harness.sent) >= 1
    assert "keep going" in harness.sent[0]
    s.stop_loop()


@pytest.mark.asyncio
async def test_interrupt_clears_the_loop():
    """Without this Esc is useless — the loop re-fires the instant the
    interrupted turn ends."""
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=10)
    await asyncio.sleep(0)
    await s.interrupt()
    assert s.loop_status() is None


class BoomHarness(FakeHarness):
    def __init__(self):
        super().__init__()
        self.event_calls = 0

    async def events(self):
        self.event_calls += 1
        raise RuntimeError("harness exploded")
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_harness_error_stops_the_loop():
    """Otherwise a broken session spins on its own error forever.

    max_iterations is set far above what _settle could burn through, so a
    cleared loop here means the error stopped it — not the cap.
    """
    harness = BoomHarness()
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=1000)
    await _settle(s)
    assert s.loop_status() is None
    assert s.last_error is not None
    assert harness.event_calls == 1     # errored once, did not spin


# --------------------------------------------------------------------------
# Task 4 — LoopService
# --------------------------------------------------------------------------
from aegis.queue import LoopService          # noqa: E402


class FakeSM:
    def __init__(self, sessions):
        self._by_handle = {s.handle: s for s in sessions}

    def get(self, handle):
        return self._by_handle.get(handle)


@pytest.mark.asyncio
async def test_service_arm_routes_to_session():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = svc.arm(from_handle="h", text="keep going", max_iterations=4)
    assert res["armed"] is True
    assert res["max_iterations"] == 4
    assert s.loop_status()["text"] == "keep going"
    s.stop_loop()


def test_service_unknown_handle_errors():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="nope", text="x")
    assert "error" in svc.stop(from_handle="nope")
    assert "error" in svc.status(from_handle="nope")


@pytest.mark.asyncio
async def test_service_stop_and_status():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    svc.arm(from_handle="h", text="keep going", max_iterations=9)
    assert svc.status(from_handle="h")["loop"]["max_iterations"] == 9
    assert svc.stop(from_handle="h", reason="done")["stopped"] is True
    assert svc.status(from_handle="h")["loop"] is None
    assert svc.stop(from_handle="h")["stopped"] is False


def test_service_rejects_bad_max_iterations():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="h", text="x", max_iterations=0)
    assert "error" in svc.arm(from_handle="h", text="x", max_iterations=-3)


def test_service_rejects_empty_text():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="h", text="   ")


@pytest.mark.asyncio
async def test_tui_bridge_exposes_loop_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.loop_service, LoopService)
        handle = app._active.handle
        assert app.loop_service.arm(
            from_handle=handle, text="keep going")["armed"] is True
        app.loop_service.stop(from_handle=handle)


# --------------------------------------------------------------------------
# Task 5 — MCP surface
# --------------------------------------------------------------------------
from aegis.mcp.server import BRIEFING, build_server      # noqa: E402


class StubBridge:
    def __init__(self, svc):
        self.loop_service = svc


async def _call(server, tool_name: str, **kwargs):
    """Same shape as the helper in tests/test_reminder.py."""
    res = await server.call_tool(tool_name, kwargs)
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    return getattr(res, "data", res)


@pytest.mark.asyncio
async def test_loop_stop_tool_registered():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    server = build_server(StubBridge(LoopService(FakeSM([s]))))
    names = {t.name for t in await server.list_tools()}
    assert "aegis_loop_stop" in names


@pytest.mark.asyncio
async def test_mcp_loop_stop_reaps():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    svc.arm(from_handle="h", text="keep going")
    server = build_server(StubBridge(svc))
    res = await _call(server, "aegis_loop_stop", from_handle="h",
                      reason="green")
    assert res["stopped"] is True
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_mcp_loop_stop_without_a_loop_is_harmless():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    server = build_server(StubBridge(LoopService(FakeSM([s]))))
    res = await _call(server, "aegis_loop_stop", from_handle="h")
    assert res["stopped"] is False


def test_briefing_mentions_loop_stop():
    assert "aegis_loop_stop" in BRIEFING


# --------------------------------------------------------------------------
# Task 6 — the /loop slash command
# --------------------------------------------------------------------------
from aegis.commands import CommandContext, dispatch      # noqa: E402


def _ctx(svc, handle="h"):
    return CommandContext(bridge=StubBridge(svc), handle=handle)


@pytest.mark.asyncio
async def test_slash_loop_arms():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop fix the failing tests", _ctx(svc))
    assert res.ok
    assert s.loop_status()["text"] == "fix the failing tests"
    assert s.loop_status()["max_iterations"] == 20
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_max_flag():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop --max 3 fix the failing tests", _ctx(svc))
    assert res.ok
    assert s.loop_status()["max_iterations"] == 3
    assert s.loop_status()["text"] == "fix the failing tests"
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_max_inside_text_survives():
    """The greedy positional stops flag parsing, so --max in the instruction
    is part of the instruction."""
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    await dispatch("/loop run bench --max 5 until it clears", _ctx(svc))
    assert "--max 5" in s.loop_status()["text"]
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_stop_is_exact_match_only():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    # More than the bare word -> an instruction, not the verb.
    await dispatch("/loop stop the dev server and restart it", _ctx(svc))
    assert s.loop_status()["text"] == "stop the dev server and restart it"
    # The bare word -> the verb.
    res = await dispatch("/loop stop", _ctx(svc))
    assert res.ok
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_slash_loop_status_and_empty_cases():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop", _ctx(svc))
    assert res.ok and "no loop" in res.title.lower()
    res = await dispatch("/loop stop", _ctx(svc))
    assert res.ok is False
    await dispatch("/loop keep going", _ctx(svc))
    res = await dispatch("/loop", _ctx(svc))
    assert res.ok and "keep going" in res.body
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_rejects_bad_max():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop --max 0 keep going", _ctx(svc))
    assert res.ok is False
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_slash_loop_arming_twice_replaces():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    await dispatch("/loop first", _ctx(svc))
    res = await dispatch("/loop second", _ctx(svc))
    assert res.ok
    assert "replaced" in res.title.lower()
    assert s.loop_status()["text"] == "second"
    s.stop_loop()
