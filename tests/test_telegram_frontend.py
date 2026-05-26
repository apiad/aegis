from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.telegram.frontend import TelegramFrontend
from aegis.tui.state import AgentState


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
        self._mid = 0

    async def send_message(self, c, t, markdown=False):
        self._mid += 1
        self.sent.append((c, t))
        return self._mid

    async def edit_message(self, c, m, t):
        self.edits.append((m, t))


def mgr():
    return SessionManager(
        {"default": 1, "researcher": 2}, "default",
        lambda p, u, h: FakeHarness(), mcp=None,
    )


def fe(bot, m):
    return TelegramFrontend(bot, m, None, None, chat_id=99,
                            auto_prompt="BE BRIEF",
                            refresh_interval=0.0)


@pytest.mark.asyncio
async def test_wrong_chat_dropped():
    b = FakeBot()
    f = fe(b, mgr())
    await f.handle_update({"message": {"chat": {"id": 1}, "text": "/new"}})
    assert b.sent == []


@pytest.mark.asyncio
async def test_new_spawns_and_sets_active():
    b = FakeBot()
    m = mgr()
    f = fe(b, m)
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/new researcher"}})
    assert len(m.list_sessions()) == 1
    assert "spawned" in b.sent[-1][1]


@pytest.mark.asyncio
async def test_bare_text_appends_auto_prompt():
    b = FakeBot()
    m = mgr()
    f = fe(b, m)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    s = m.list_sessions()[0]
    core = m.get(s.handle)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "do x"}})
    await asyncio.sleep(0)
    assert core._session.sent[0] == "do x\n\n[BE BRIEF]"


@pytest.mark.asyncio
async def test_unknown_slug_lists_agents():
    b = FakeBot()
    f = fe(b, mgr())
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/new bogus"}})
    assert "researcher" in b.sent[-1][1]


class SlowHarness:
    def __init__(self):
        self.sent: list[str] = []

    async def start(self): ...
    async def send(self, t):
        self.sent.append(t)
    async def close(self): ...

    async def events(self):
        from aegis.events import AssistantText, Result
        await asyncio.sleep(0.05)
        yield AssistantText(text="hi")
        await asyncio.sleep(0.05)
        yield Result(duration_ms=10, is_error=False)


@pytest.mark.asyncio
async def test_mid_turn_refresher_edits_status_repeatedly():
    b = FakeBot()
    m = SessionManager({"default": 1}, "default",
                       lambda p, u, h: SlowHarness(), mcp=None)
    f = TelegramFrontend(b, m, None, None, chat_id=99, auto_prompt="",
                         refresh_interval=0.01)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    core = m.list_sessions()[0]
    core = m.get(core.handle)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    # drain the turn
    for _ in range(40):
        await asyncio.sleep(0.005)
        if core.state is not AgentState.working:
            break
    # the turn took ~100ms with refresh_interval=10ms — many edits
    assert len(b.edits) >= 2


@pytest.mark.asyncio
async def test_handle_oneshot_does_not_move_sticky():
    b = FakeBot()
    m = mgr()
    f = fe(b, m)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h1 = m.list_sessions()[0].handle
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h2 = [s.handle for s in m.list_sessions() if s.handle != h1][0]
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": f"/{h1} ping"}})
    assert f._active == h2  # sticky still on the most recent


@pytest.mark.asyncio
async def test_sessions_line_is_tappable():
    b = FakeBot()
    m = mgr()
    f = fe(b, m)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h = m.list_sessions()[0].handle              # hyphenated, e.g. amber-floyd
    await f.handle_update(
        {"message": {"chat": {"id": 99}, "text": "/sessions"}})
    line = b.sent[-1][1]
    assert f"/{h.replace('-', '_')}" in line      # tappable underscore form
    assert "\n" in line or len(m.list_sessions()) == 1  # one per line


@pytest.mark.asyncio
async def test_underscore_handle_switches_sticky():
    b = FakeBot()
    m = mgr()
    f = fe(b, m)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h1 = m.list_sessions()[0].handle
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    # switch back to the first using the tappable underscore alias
    alias = "/" + h1.replace("-", "_")
    await f.handle_update({"message": {"chat": {"id": 99}, "text": alias}})
    assert f._active == h1


def test_frontend_ctor_accepts_bridge_and_cfg():
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
        chat_id=12345, auto_prompt="")
    assert fe._bridge is not None
    assert fe._cfg is not None
