from __future__ import annotations

import asyncio

import pytest

from aegis.core.manager import SessionManager
from aegis.telegram.frontend import TelegramFrontend
from aegis.tui.state import AgentState


class MockBot:
    def __init__(self):
        self.calls: list[tuple] = []
        self._next_mid = 100

    async def send_message(self, chat_id, text, *, parse_mode=None):
        self._next_mid += 1
        self.calls.append(("send_message", chat_id, text, parse_mode,
                           self._next_mid))
        return self._next_mid

    async def edit_message(self, chat_id, message_id, text, *, parse_mode=None):
        self.calls.append(("edit_message", chat_id, message_id, text,
                           parse_mode))

    async def send_document(self, chat_id, path, *, caption=None,
                            parse_mode=None):
        self._next_mid += 1
        self.calls.append(("send_document", chat_id, path, caption, parse_mode,
                           self._next_mid))
        return self._next_mid

    async def get_updates(self, offset, timeout=50):
        return []


class SimpleHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        from aegis.events import AssistantText, Result, ToolUse
        yield ToolUse(name="ToolA", summary="")
        yield ToolUse(name="ToolB", summary="")
        yield AssistantText(text="**hello** world")
        yield Result(duration_ms=10, is_error=False)


class BigHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        from aegis.events import AssistantText, Result
        big = "\n\n".join(f"para{i}-" + "x" * 3500 for i in range(5))
        yield AssistantText(text=big)
        yield Result(duration_ms=10, is_error=False)


async def _drain(core):
    for _ in range(120):
        await asyncio.sleep(0.005)
        if core.state is not AgentState.working:
            return
    raise AssertionError("turn did not finish")


@pytest.mark.asyncio
async def test_e2e_simple_reply(tmp_path):
    bot = MockBot()
    mgr = SessionManager({"default": 1}, "default",
                         lambda a, u, h: SimpleHarness(), mcp=None)
    f = TelegramFrontend(bot, mgr, None, None, chat_id=99,
                         auto_prompt="", state_dir=tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h = mgr.list_sessions()[0].handle
    core = mgr.get(h)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    await _drain(core)

    kinds = [c[0] for c in bot.calls]
    # /new ack send_message; ticker send_message; >=2 edits for ToolUses
    # + one final edit on finish; final reply send_message.
    assert kinds[0] == "send_message"           # /new ack
    assert "send_message" in kinds[1:]          # ticker open
    assert kinds.count("edit_message") >= 2
    # last call is the rendered reply
    last = bot.calls[-1]
    assert last[0] == "send_message"
    assert last[3] == "HTML"
    assert "<b>hello</b>" in last[2]


@pytest.mark.asyncio
async def test_e2e_overflow_reply(tmp_path):
    bot = MockBot()
    mgr = SessionManager({"default": 1}, "default",
                         lambda a, u, h: BigHarness(), mcp=None)
    f = TelegramFrontend(bot, mgr, None, None, chat_id=99,
                         auto_prompt="", state_dir=tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h = mgr.list_sessions()[0].handle
    core = mgr.get(h)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    await _drain(core)

    docs = [c for c in bot.calls if c[0] == "send_document"]
    assert len(docs) == 1
    assert docs[0][1] == 99
    assert docs[0][4] == "HTML"


@pytest.mark.asyncio
async def test_e2e_two_frontends_share_session(tmp_path):
    bot = MockBot()
    mgr = SessionManager({"default": 1}, "default",
                         lambda a, u, h: SimpleHarness(), mcp=None)
    f = TelegramFrontend(bot, mgr, None, None, chat_id=99,
                         auto_prompt="", state_dir=tmp_path)
    await f.handle_update({"message": {"chat": {"id": 99}, "text": "/new"}})
    h = mgr.list_sessions()[0].handle
    core = mgr.get(h)

    seen_events: list = []
    seen_states: list = []
    core.add_event_observer(lambda c, ev: seen_events.append(ev))
    core.add_state_observer(lambda c, st, fin: seen_states.append((st, fin)))

    await f.handle_update({"message": {"chat": {"id": 99}, "text": "go"}})
    await _drain(core)

    # Frontend saw enough to render: at least one edit_message.
    assert any(c[0] == "edit_message" for c in bot.calls)
    # Side observers saw events independently.
    from aegis.events import AssistantText, Result, ToolUse
    assert any(isinstance(e, ToolUse) for e in seen_events)
    assert any(isinstance(e, AssistantText) for e in seen_events)
    assert any(isinstance(e, Result) for e in seen_events)
    # Both transitions: working then ready.
    assert (AgentState.working, False) in seen_states
    assert (AgentState.ready, True) in seen_states
