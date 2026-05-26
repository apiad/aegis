from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.telegram.frontend import TelegramFrontend


class FakeHarness:
    async def start(self): ...
    async def send(self, t):
        self.sent = getattr(self, "sent", []) + [t]
    async def close(self): ...

    async def events(self):
        if False:
            yield


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, str]] = []
        self.documents: list[tuple] = []
        self._mid = 0

    async def send_message(self, c, t, *, parse_mode=None):
        self._mid += 1
        self.sent.append((c, t))
        return self._mid

    async def edit_message(self, c, m, t, *, parse_mode=None):
        self.edits.append((m, t))

    async def send_document(self, c, path, *, caption=None, parse_mode=None):
        self._mid += 1
        self.documents.append((c, path, caption, parse_mode))
        return self._mid


def mgr():
    return SessionManager(
        {"default": 1, "researcher": 2}, "default",
        lambda p, u, h: FakeHarness(), mcp=None,
    )


def fe(bot, m, tmp_path):
    return TelegramFrontend(bot, m, None, None, chat_id=99,
                            auto_prompt="BE BRIEF",
                            state_dir=tmp_path)


@pytest.mark.asyncio
async def test_wrong_chat_dropped(tmp_path):
    b = FakeBot()
    f = fe(b, mgr(), tmp_path)
    await f.handle_update({"message": {"chat": {"id": 1}, "text": "/new"}})
    assert b.sent == []


@pytest.mark.asyncio
async def test_new_spawns_and_sets_active(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/new researcher"}})
    assert len(m.list_sessions()) == 1
    assert "spawned" in b.sent[-1][1]


@pytest.mark.asyncio
async def test_bare_text_appends_auto_prompt(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    s = m.list_sessions()[0]
    core = m.get(s.handle)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "do x"}})
    await asyncio.sleep(0)
    assert core._session.sent[0] == "do x\n\n[BE BRIEF]"


@pytest.mark.asyncio
async def test_unknown_slug_lists_agents(tmp_path):
    b = FakeBot()
    f = fe(b, mgr(), tmp_path)
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/new bogus"}})
    assert "researcher" in b.sent[-1][1]


@pytest.mark.asyncio
async def test_handle_oneshot_does_not_move_sticky(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h1 = m.list_sessions()[0].handle
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h2 = [s.handle for s in m.list_sessions() if s.handle != h1][0]
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": f"/{h1} ping"}})
    assert f._active == h2  # sticky still on the most recent


@pytest.mark.asyncio
async def test_sessions_line_is_tappable(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h = m.list_sessions()[0].handle
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/sessions"}})
    line = b.sent[-1][1]
    assert f"/{h.replace('-', '_')}" in line
    assert "\n" in line or len(m.list_sessions()) == 1


@pytest.mark.asyncio
async def test_underscore_handle_switches_sticky(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h1 = m.list_sessions()[0].handle
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    alias = "/" + h1.replace("-", "_")
    await f.handle_update({"message": {"chat": {"id": 99}, "text": alias}})
    assert f._active == h1


def test_frontend_ctor_accepts_bridge_and_cfg(tmp_path):
    """v0.10: TelegramFrontend gains bridge + cfg constructor params."""
    from aegis.telegram.frontend import TelegramFrontend

    class _FakeBot:
        async def send_message(self, *a, **k): return 1
        async def edit_message(self, *a, **k): return None

    class _FakeBridge:
        queue_manager = None
        scheduler = None

    class _FakeCfg:
        remotes = {}

    class _FakeMgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    fe = TelegramFrontend(
        _FakeBot(), _FakeMgr(), _FakeBridge(), _FakeCfg(),
        chat_id=12345, auto_prompt="", state_dir=tmp_path)
    assert fe._bridge is not None
    assert fe._cfg is not None


class ToolBurstHarness:
    def __init__(self):
        self.sent: list[str] = []

    async def start(self): ...
    async def send(self, t):
        self.sent.append(t)
    async def close(self): ...

    async def events(self):
        from aegis.events import AssistantText, Result, ToolUse
        yield ToolUse(name="ToolA", summary="")
        yield ToolUse(name="ToolB", summary="")
        yield AssistantText(text="done")
        yield Result(duration_ms=10, is_error=False)


@pytest.mark.asyncio
async def test_ticker_edits_on_tool_use(tmp_path):
    b = FakeBot()
    m = SessionManager({"default": 1}, "default",
                       lambda p, u, h: ToolBurstHarness(), mcp=None)
    f = TelegramFrontend(b, m, None, None, chat_id=99, auto_prompt="",
                         state_dir=tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    core = m.list_sessions()[0]
    core = m.get(core.handle)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    # drain turn
    from aegis.tui.state import AgentState
    for _ in range(80):
        await asyncio.sleep(0.005)
        if core.state is not AgentState.working:
            break
    # one send for the initial ticker, then edits for each ToolUse and final.
    # Then a send for the reply at finish.
    assert len(b.edits) >= 2, f"edits={b.edits}"
    # The reply was sent (last send_message after the status one).
    assert any("done" in s[1] for s in b.sent)


class OverflowHarness:
    def __init__(self):
        self.sent: list[str] = []

    async def start(self): ...
    async def send(self, t):
        self.sent.append(t)
    async def close(self): ...

    async def events(self):
        from aegis.events import AssistantText, Result
        # Five 4k paragraphs — chunker emits 5 parts, which exceeds max_parts=3,
        # forcing spillover.
        big = "\n\n".join(f"para{i}-" + "x" * 3500 for i in range(5))
        yield AssistantText(text=big)
        yield Result(duration_ms=10, is_error=False)


@pytest.mark.asyncio
async def test_overflow_replies_as_send_document(tmp_path):
    b = FakeBot()
    m = SessionManager({"default": 1}, "default",
                       lambda p, u, h: OverflowHarness(), mcp=None)
    f = TelegramFrontend(b, m, None, None, chat_id=99, auto_prompt="",
                         state_dir=tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    core = m.list_sessions()[0]
    core = m.get(core.handle)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    from aegis.tui.state import AgentState
    for _ in range(80):
        await asyncio.sleep(0.005)
        if core.state is not AgentState.working:
            break
    assert len(b.documents) == 1
    chat_id, path, caption, parse_mode = b.documents[0]
    assert chat_id == 99
    assert parse_mode == "HTML"
    assert path.parent.name == "overflow"
    assert path.name.startswith("aegis-reply-")
    assert path.suffix == ".md"
    assert "📎" in caption


@pytest.mark.asyncio
async def test_envelope_shows_on_ticker(tmp_path):
    from aegis.queue.schema import InboxMessage
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    core = m.list_sessions()[0]
    core = m.get(core.handle)
    f._attach_observers(core)
    msg = InboxMessage(sender="agent:foo", timestamp="2026-05-26T00:00:00Z",
                       body="hi")
    # Fire the inbox observer synchronously.
    for cb in core._extra_inbox_observers:
        cb(core, msg)
    state = f._state_for(core.handle)
    assert state["envelope"] == "from agent:foo"
    ticker = f._render_ticker(core, state)
    assert "✉️" in ticker
    assert "agent:foo" in ticker


@pytest.mark.asyncio
async def test_two_sessions_have_independent_state(tmp_path):
    b = FakeBot()
    m = mgr()
    f = fe(b, m, tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h1 = m.list_sessions()[0].handle
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h2 = [s.handle for s in m.list_sessions() if s.handle != h1][0]
    core1 = m.get(h1)
    core2 = m.get(h2)
    f._attach_observers(core1)
    f._attach_observers(core2)
    f._state_for(h1)["mid"] = 111
    f._state_for(h2)["mid"] = 222
    assert f._states[h1]["mid"] == 111
    assert f._states[h2]["mid"] == 222
    assert f._states[h1] is not f._states[h2]
