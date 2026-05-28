"""AcpSession hermetic tests via stub ACP agent subprocesses.

Each stub is a tiny Python script that imports the official ACP SDK,
runs a subclass of ``acp.Agent`` over stdio, and responds to prompts
with scripted behavior. The aegis ``AcpSession`` connects to it as a
real subprocess and exercises the full session lifecycle.

This gives hermetic coverage of:
- initialize + new_session + prompt → AssistantText + Result
- Multi-turn state across multiple sends on the same session
- Per-session MCP injection (session/new mcp_servers param)
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from aegis.config import Agent, GeminiCLI
from aegis.drivers.acp import AcpDriver
from aegis.events import AssistantText, Result


# ---------- Stub ACP agent scripts ------------------------------------

_STUB_OK = r'''
import asyncio
import sys
import acp
from acp.schema import AgentMessageChunk, TextContentBlock


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True,
                               "mcpCapabilities": {"http": True}},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text="OK", type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''

_STUB_REMEMBER = r'''
import asyncio
import re
import sys
import acp
from acp.schema import AgentMessageChunk, TextContentBlock


class StubAgent(acp.Agent):
    def __init__(self):
        self.memory = None

    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        text = ""
        for block in prompt:
            if getattr(block, "type", None) == "text":
                text = block.text
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block["text"]
        if "remember" in text.lower():
            m = re.search(r"\d+", text)
            if m:
                self.memory = m.group()
            reply = "OK"
        elif "recall" in text.lower():
            reply = self.memory or "none"
        else:
            reply = "?"
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text=reply, type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''

_STUB_ECHO_MCP = r'''
import asyncio
import json
import sys
import acp
from acp.schema import AgentMessageChunk, TextContentBlock


class StubAgent(acp.Agent):
    def __init__(self):
        self.last_mcp = []

    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True,
                               "mcpCapabilities": {"http": True}},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        self.last_mcp = [m.model_dump(mode="json")
                          for m in (mcp_servers or [])]
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        text = json.dumps(self.last_mcp)
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text=text, type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


# ---------- Stub driver helpers ---------------------------------------

def _stub_driver(script: str) -> AcpDriver:
    class _D(AcpDriver):
        BASE_CMD = [sys.executable, "-c", script]
        def build_argv(self, *a, **kw): return list(self.BASE_CMD)
    return _D()


def _agent() -> Agent:
    return Agent(provider=GeminiCLI(model=""))


# ---------- Tests -----------------------------------------------------

def test_acp_sdk_receive_timeout_race_workaround_active():
    """The ACP SDK 0.10.0 has a race: Connection.__init__ starts the
    receive loop before assigning self._receive_timeout. Under
    aggressive task scheduling (real-terminal Textual loop) the loop
    can run first and crash with AttributeError, killing the receive
    loop → 'Connection closed' on the next initialize().

    aegis.drivers.acp installs a class-level default that makes the
    attribute lookup safe even before __init__ finishes. This guards
    that the workaround stays in place across refactors / SDK upgrades."""
    import aegis.drivers.acp  # noqa: F401 — triggers the monkey-patch
    from acp.connection import Connection
    bare = Connection.__new__(Connection)
    # Must not AttributeError; default is None (no read timeout).
    assert bare._receive_timeout is None


async def test_acp_session_basic_round_trip(tmp_path):
    """initialize + new_session + prompt → AssistantText + Result."""
    sess = _stub_driver(_STUB_OK).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("hello")
    events = [ev async for ev in sess.events()]
    await sess.close()

    text_events = [e for e in events if isinstance(e, AssistantText)]
    result_events = [e for e in events if isinstance(e, Result)]
    assert any("OK" in e.text for e in text_events), events
    assert len(result_events) == 1
    assert result_events[0].is_error is False


async def test_acp_session_multi_turn_state_survives(tmp_path):
    """Two consecutive send() calls on the same session — agent state
    (the remembered number) survives across turns."""
    sess = _stub_driver(_STUB_REMEMBER).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()

    # Turn 1: ask the stub to remember a number
    await sess.send("Please remember 4217")
    turn1 = [ev async for ev in sess.events()]
    assert any(isinstance(e, AssistantText) and "OK" in e.text
               for e in turn1)

    # Turn 2: recall — fresh send(), same session_id
    await sess.send("Please recall the number")
    turn2 = [ev async for ev in sess.events()]
    await sess.close()
    assert any(isinstance(e, AssistantText) and "4217" in e.text
               for e in turn2), turn2


async def test_acp_session_injects_mcp_servers_into_new_session(tmp_path):
    """AcpSession passes mcp_servers correctly during new_session.
    Stub agent stores them and echoes back as JSON on first prompt."""
    sess = _stub_driver(_STUB_ECHO_MCP).session(
        _agent(), str(tmp_path),
        mcp_url="http://127.0.0.1:9999/mcp", handle="h")
    await sess.start()
    await sess.send("echo back the mcp_servers you got")
    events = [ev async for ev in sess.events()]
    await sess.close()

    text = next((e.text for e in events if isinstance(e, AssistantText)),
                "")
    parsed = json.loads(text)
    assert len(parsed) == 1
    assert parsed[0]["type"] == "http"
    assert parsed[0]["name"] == "aegis"
    assert parsed[0]["url"] == "http://127.0.0.1:9999/mcp"
    # headers may serialize as [] or as some structured form depending on
    # the schema — accept either as long as it's present.
    assert "headers" in parsed[0]


_STUB_RESUME = r'''
import asyncio
import acp
from acp.schema import AgentMessageChunk, TextContentBlock


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn
        self.loaded = None

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def load_session(self, cwd, session_id, mcp_servers=None,
                            additional_directories=None, **kw):
        self.loaded = session_id
        return acp.LoadSessionResponse()

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        reply = f"resumed={self.loaded}|sid={session_id}"
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text=reply, type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


def test_acp_driver_supports_resume():
    assert AcpDriver.supports_resume is True


async def test_acp_session_load_session_when_resume_id_set(tmp_path):
    """driver.resume(...) yields a session whose start() invokes
    load_session(session_id=...) instead of new_session(...)."""
    drv = _stub_driver(_STUB_RESUME)
    sess = drv.resume(_agent(), str(tmp_path), mcp_url="",
                      handle="h", session_id="prior-sid-9000")
    await sess.start()
    await sess.send("ping")
    events = [ev async for ev in sess.events()]
    await sess.close()
    text = next((e.text for e in events if isinstance(e, AssistantText)), "")
    assert "resumed=prior-sid-9000" in text
    assert "sid=prior-sid-9000" in text


_STUB_WITH_USAGE = r'''
import asyncio
import acp
from acp.schema import (
    AgentMessageChunk, TextContentBlock, Usage,
)


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text="OK", type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        return acp.PromptResponse(
            stopReason="end_turn",
            usage=Usage(
                inputTokens=1234,
                outputTokens=567,
                cachedReadTokens=2000,
                cachedWriteTokens=300,
                thoughtTokens=42,
                totalTokens=1234 + 567 + 2000 + 300 + 42,
            ),
        )

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


async def test_acp_session_maps_usage_into_result_token_usage(tmp_path):
    """ACP PromptResponse.usage must land on Result.usage as TokenUsage so
    SessionMetrics.commit (which reads ev.usage) sees real numbers.
    Pre-fix: Result.usage was None → metrics stayed at 0/0/0/0 for every
    Gemini and OpenCode session.

    Mapping:
        input_tokens         → TokenUsage.input
        cached_write_tokens  → TokenUsage.cache_creation
        cached_read_tokens   → TokenUsage.cache_read
        output_tokens + thought_tokens → TokenUsage.output
    (thought tokens fold into output because they're billed at the
    output rate by every provider aegis surfaces today.)
    """
    sess = _stub_driver(_STUB_WITH_USAGE).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("hi")
    events = [ev async for ev in sess.events()]
    await sess.close()

    from aegis.events import Result, TokenUsage
    result = next(e for e in events if isinstance(e, Result))
    assert result.usage is not None
    assert isinstance(result.usage, TokenUsage)
    assert result.usage.input == 1234
    assert result.usage.output == 567 + 42       # thought_tokens folded in
    assert result.usage.cache_read == 2000
    assert result.usage.cache_creation == 300
    assert result.usage.true_input == 1234 + 300 + 2000  # 3534


_STUB_FAILED_TOOL = r'''
import asyncio
import acp
from acp.schema import (
    ToolCallStart, ToolCallProgress,
    ContentToolCallContent, TextContentBlock,
)


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallStart(
                toolCallId="tc-fail-1",
                title="bad_edit",
                kind="edit",
                status="in_progress",
                sessionUpdate="tool_call",
            ),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallProgress(
                toolCallId="tc-fail-1",
                title="bad_edit",
                status="failed",
                content=[ContentToolCallContent(
                    content=TextContentBlock(
                        text="permission denied", type="text"),
                    type="content",
                )],
                sessionUpdate="tool_call_update",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


async def test_acp_failed_tool_marks_error(tmp_path):
    """ToolCallProgress(status='failed') must produce ToolResult(is_error=True).

    Pre-fix the driver hardcoded is_error=False on every completed/failed
    tool call — failures rendered as green ok lines."""
    from aegis.events import ToolResult
    sess = _stub_driver(_STUB_FAILED_TOOL).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("trigger a failure")
    events = [ev async for ev in sess.events()]
    await sess.close()

    results = [e for e in events if isinstance(e, ToolResult)]
    assert len(results) == 1, events
    assert results[0].is_error is True
    assert "permission denied" in results[0].text


_STUB_GEMINI_QUOTA = r'''
import asyncio
import acp
from acp.schema import AgentMessageChunk, TextContentBlock


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                content=TextContentBlock(text="ok", type="text"),
                sessionUpdate="agent_message_chunk",
            ),
        )
        # Gemini puts token counts in field_meta.quota, NOT in usage —
        # leaving usage=None means aegis would report 0/0 unless we
        # fall back to field_meta. Wire alias for field_meta is _meta.
        return acp.PromptResponse(**{
            "stopReason": "end_turn",
            "_meta": {"quota": {"token_count": {
                "input_tokens": 12345, "output_tokens": 678}}},
        })

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


_STUB_TOOL_LIFECYCLE = r'''
import asyncio
import acp
from acp.schema import (
    ToolCallStart, ToolCallProgress,
    ContentToolCallContent, TextContentBlock, ToolCallLocation,
)


class StubAgent(acp.Agent):
    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": True},
            agentInfo={"name": "stub", "version": "0.0.1"},
        )

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId="sess-1")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallStart(
                toolCallId="tc-read-1",
                title="read",
                kind="read",
                status="in_progress",
                locations=[ToolCallLocation(path="/tmp/foo.txt", line=42)],
                rawInput={"filePath": "/tmp/foo.txt"},
                sessionUpdate="tool_call",
            ),
        )
        await self._conn.session_update(
            session_id=session_id,
            update=ToolCallProgress(
                toolCallId="tc-read-1",
                title="/tmp/foo.txt",
                status="completed",
                content=[ContentToolCallContent(
                    content=TextContentBlock(
                        text="file contents", type="text"),
                    type="content",
                )],
                sessionUpdate="tool_call_update",
            ),
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


asyncio.run(acp.run_agent(StubAgent()))
'''


async def test_acp_tool_use_carries_kind_locations_and_raw_input(tmp_path):
    """ToolCallStart with kind/locations/raw_input populated must land
    on the canonical ToolUse with the same fields, so the renderer can
    pick a kind icon and a pathhint without driver branching."""
    from aegis.events import ToolUse
    sess = _stub_driver(_STUB_TOOL_LIFECYCLE).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("read the file")
    events = [ev async for ev in sess.events()]
    await sess.close()

    uses = [e for e in events if isinstance(e, ToolUse)]
    assert len(uses) == 1
    u = uses[0]
    assert u.kind == "read"
    assert u.tool_call_id == "tc-read-1"
    assert u.locations == (("/tmp/foo.txt", 42),)
    assert u.raw_input == {"filePath": "/tmp/foo.txt"}
    assert u.status == "in_progress"


async def test_acp_tool_result_correlates_kind_via_tool_call_id(tmp_path):
    """ToolCallProgress(completed) doesn't carry kind on its own — but
    we stash kind from the matching ToolCallStart on the client so the
    emitted ToolResult can fill it in."""
    from aegis.events import ToolResult
    sess = _stub_driver(_STUB_TOOL_LIFECYCLE).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("read the file")
    events = [ev async for ev in sess.events()]
    await sess.close()

    results = [e for e in events if isinstance(e, ToolResult)]
    assert len(results) == 1
    r = results[0]
    assert r.is_error is False
    assert r.tool_call_id == "tc-read-1"
    assert r.kind == "read"
    assert r.text == "file contents"


async def test_acp_session_uses_field_meta_quota_fallback(tmp_path):
    """Gemini puts token counts in PromptResponse.field_meta.quota.token_count
    rather than PromptResponse.usage. Without a fallback the driver
    reports Result.usage=None → SessionMetrics shows 0/0 for every
    Gemini turn. Fix: when usage is None, read field_meta.quota."""
    from aegis.events import Result, TokenUsage
    sess = _stub_driver(_STUB_GEMINI_QUOTA).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("hi")
    events = [ev async for ev in sess.events()]
    await sess.close()

    result = next(e for e in events if isinstance(e, Result))
    assert result.usage is not None
    assert isinstance(result.usage, TokenUsage)
    assert result.usage.input == 12345
    assert result.usage.output == 678
    assert result.input_tokens == 12345
    assert result.output_tokens == 678


async def test_acp_session_empty_mcp_url_sends_no_mcp_servers(tmp_path):
    """If mcp_url is empty, mcp_servers=[]."""
    sess = _stub_driver(_STUB_ECHO_MCP).session(
        _agent(), str(tmp_path), mcp_url="", handle="h")
    await sess.start()
    await sess.send("echo")
    events = [ev async for ev in sess.events()]
    await sess.close()

    text = next((e.text for e in events if isinstance(e, AssistantText)),
                "")
    parsed = json.loads(text)
    assert parsed == []
