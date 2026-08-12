"""A renamed agent has to be told, and it must cost nothing at rest."""
from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.core.session import AgentSession
from aegis.events import Result
from aegis.queue.inbox import InboxRouter
from aegis.tui.state import AgentState


class FakeHarness:
    """Records what text each turn actually sent to the harness — the
    substrate this feature is about, rather than the session's internals."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False, usage=None)


def _session() -> tuple[AgentSession, FakeHarness]:
    h = FakeHarness()
    return AgentSession(h, None, "default", "old-name"), h


@pytest.mark.asyncio
async def test_notice_rides_the_next_turn():
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    await s.send("hello")
    await s._task

    assert len(h.sent) == 1
    text = h.sent[0]
    assert "old-name" in text and "new-name" in text
    assert "hello" in text, "the operator's own message must survive"
    assert text.index("new-name") < text.index("hello"), (
        "the notice must arrive BEFORE the agent acts, not after")


@pytest.mark.asyncio
async def test_notice_does_not_start_a_turn():
    """The property that matters most. Renaming an idle agent must not
    wake it — this is what a later 'simplification' back into deliver()
    would break."""
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    assert s.state is AgentState.ready
    assert h.sent == [], "a rename must not bill an LLM turn"


@pytest.mark.asyncio
async def test_notice_fires_exactly_once():
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")

    await s.send("first")
    await s._task
    await s.send("second")
    await s._task

    assert "new-name" in h.sent[0]
    assert "new-name" not in h.sent[1], "the notice must not repeat"


@pytest.mark.asyncio
async def test_notice_names_the_consequence():
    """'You were renamed' invites a shrug. The text has to say the old
    handle no longer routes, or an agent notes it and moves on."""
    s, h = _session()
    s.note_rename("old-name", "new-name", by="operator")
    await s.send("go")
    await s._task

    text = h.sent[0]
    assert "from_handle" in text
    assert "operator" in text


@pytest.mark.asyncio
async def test_rename_mid_turn_lands_on_the_next_turn():
    """A rename while a turn is in flight must not alter that turn's text.
    The harness blocks so the ordering is deterministic rather than a race
    against how fast the fake yields its Result."""
    released = asyncio.Event()

    class BlockingHarness(FakeHarness):
        async def send(self, text: str) -> None:
            self.sent.append(text)
            await released.wait()

    h = BlockingHarness()
    s = AgentSession(h, None, "default", "old-name")

    await s.send("first")
    for _ in range(1000):          # let the turn reach the harness
        if h.sent:
            break
        await asyncio.sleep(0)
    assert h.sent == ["first"], "the turn's text is fixed before the rename"

    s.note_rename("old-name", "new-name", by="operator")
    released.set()
    await s._task
    assert "new-name" not in h.sent[0], "a running turn must not be rewritten"

    await s.send("second")
    await s._task
    assert "new-name" in h.sent[1], "the notice rides the turn after"


@pytest.mark.asyncio
async def test_notice_is_visible_to_the_operator():
    """It has to reach the transcript too, or 'why did it use the old
    name' is undebuggable."""
    s, h = _session()
    seen: list[str] = []
    s.add_dispatch_observer(lambda _s, batch: seen.extend(
        m.sender for m in batch))

    s.note_rename("old-name", "new-name", by="operator")
    await s.send("go")
    await s._task

    assert "substrate" in seen


class _Harness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr() -> SessionManager:
    return SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: _Harness(),
        mcp=None, inbox=InboxRouter(),
    )


@pytest.mark.asyncio
async def test_manager_rename_by_operator_notices():
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name", by="operator")
    assert len(s._pending_notices) == 1
    assert "new-name" in s._pending_notices[0].body


@pytest.mark.asyncio
async def test_manager_rename_by_agent_is_silent():
    """A self-rename already returned {ok, old, new} to the caller, and a
    peer rename is indistinguishable from it over the shared MCP port."""
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name")
    assert s._pending_notices == []


@pytest.mark.asyncio
async def test_manager_rename_default_is_silent():
    """Defaulting to `agent` means a call site that forgets `by=` produces
    a missing notice, never a false one."""
    m = _mgr()
    s = m._sync_spawn("default", handle="old-name")
    await m.rename_handle("old-name", "new-name", by="agent")
    assert s._pending_notices == []


@pytest.mark.asyncio
async def test_slash_rename_declares_the_operator():
    """The /rename command is the TUI's and the web client's shared path,
    so this one assertion covers both frontends."""
    from aegis.commands.builtins.session_ctl import _rename

    seen = {}

    class Bridge:
        async def rename_handle(self, old, new, title=None, *, by="agent"):
            seen["by"] = by
            return {"ok": True, "old": old, "new": new}

    class Ctx:
        bridge = Bridge()
        handle = "old-name"

    await _rename(Ctx(), {"new": "new-name"})
    assert seen["by"] == "operator"
