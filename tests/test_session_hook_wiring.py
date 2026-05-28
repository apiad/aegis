"""Pre_turn / post_turn hooks fire at the right point in _run_turn.

Uses a fake harness session that captures the text sent to it, so we can
assert the message reaching the harness has the composed prepend_system
inlined as a <aegis_context>...</aegis_context> block.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.hooks import hook
from aegis.hooks.contexts import PreTurnResult
from aegis.hooks.decorator import _reset_registry_for_tests


from aegis.events import AssistantText, Result

class FakeHarnessSession:
    """Minimal stand-in for HarnessSession. Captures sends."""
    def __init__(self):
        self.sent: list[str] = []
        self._events_q: asyncio.Queue = asyncio.Queue()
        self.started = False

    async def start(self): self.started = True
    async def send(self, text: str):
        self.sent.append(text)
        # Emit text then the turn result
        await self._events_q.put(AssistantText(text="response text"))
        await self._events_q.put(Result(duration_ms=10, is_error=False))

    async def events(self):
        while True:
            ev = await self._events_q.get()
            yield ev
            if isinstance(ev, Result):
                return

    async def close(self, reason: str = ""): pass


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_pre_turn_prepend_system_reaches_harness(tmp_path: Path) -> None:
    @hook("pre_turn")
    async def inject(ctx):
        return PreTurnResult(prepend_system="LOAD-X")

    from aegis.core.session import AgentSession  # late import after hook reg
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness,
        FakeAgent("p", "claude"),
        "p",
        "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("hello user")
    assert len(harness.sent) == 1
    sent = harness.sent[0]
    assert "<aegis_context>" in sent
    assert "LOAD-X" in sent
    assert "hello user" in sent


@pytest.mark.asyncio
async def test_block_short_circuits_no_send(tmp_path: Path) -> None:
    @hook("pre_turn")
    async def blocker(ctx):
        return PreTurnResult(block="not allowed")

    from aegis.core.session import AgentSession
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    result = await session.send_and_wait("hi")
    assert harness.sent == []
    assert result.blocked_reason == "not allowed"


@pytest.mark.asyncio
async def test_post_turn_fires_with_assistant_text(tmp_path: Path) -> None:
    captured = {}

    @hook("post_turn")
    async def record(ev):
        captured["text"] = ev.assistant_message
        captured["user"] = ev.user_message

    from aegis.core.session import AgentSession
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("hi")
    # Allow observer hooks to run
    await asyncio.sleep(0.05)
    assert captured.get("user") == "hi"
    assert "response" in captured.get("text", "")


@pytest.mark.asyncio
async def test_session_end_fires_on_close(tmp_path: Path) -> None:
    captured = {}

    @hook("session_end")
    async def record(ev):
        captured["reason"] = ev.reason
        captured["handle"] = ev.session.handle

    from aegis.core.session import AgentSession
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("hi")
    await session.close(reason="test-close")
    await asyncio.sleep(0.05)
    assert captured.get("reason") == "test-close"
    assert captured.get("handle") == "t"


@pytest.mark.asyncio
async def test_session_start_fires_before_first_pre_turn(tmp_path: Path) -> None:
    """session_start fires once, at the top of the first turn, BEFORE
    pre_turn. Awaited (not fire-and-forget) so the ordering across
    start → pre → harness send is deterministic."""
    timeline: list[str] = []

    @hook("session_start")
    async def s_start(ev):
        timeline.append(f"start:{ev.session.handle}")

    @hook("pre_turn")
    async def pre(ctx):
        timeline.append(f"pre:{ctx.user_message}")
        return PreTurnResult()

    from aegis.core.session import AgentSession
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("hi")
    await asyncio.sleep(0.05)
    # Order: start first, then pre. Both fire exactly once.
    assert timeline == ["start:t", "pre:hi"]


@pytest.mark.asyncio
async def test_session_start_fires_only_once_across_turns(tmp_path: Path) -> None:
    """session_start fires on the FIRST turn only — subsequent turns
    don't refire it."""
    starts: list[str] = []

    @hook("session_start")
    async def s_start(ev):
        starts.append(ev.session.handle)

    from aegis.core.session import AgentSession
    class FakeAgent:
        def __init__(self, profile, harness):
            self.profile = profile
            self.harness = harness
            self.model = "sonnet"

    harness = FakeHarnessSession()
    session = AgentSession(
        harness, FakeAgent("p", "claude"), "p", "t",
        project_root=tmp_path,
    )
    await session.send_and_wait("turn 1")
    await session.send_and_wait("turn 2")
    await asyncio.sleep(0.05)
    assert starts == ["t"]
