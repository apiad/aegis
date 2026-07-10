# Native lovelaice agent (VS1 spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A native, harness-free agent runs inside an aegis tab — driven by `lovelaice-acp` speaking official ACP v1 — that reads a file and answers, using a model API or local endpoint directly (no external CLI harness).

**Architecture:** Add a new clean-room ACP-v1 server in lovelaice on the `agent-client-protocol` SDK (`acp.Agent` + `acp.run_agent`), leaving the legacy hand-rolled `AcpServer` frozen for warden. Repoint the `lovelaice-acp` console script to the v1 server. In aegis, add a `Lovelaice` provider + a thin `LovelaiceDriver(AcpDriver)` + an `extra_env` seam to pass model/base_url/api_key to the subprocess, and add lovelaice as a dependency.

**Tech Stack:** Python 3.13, `agent-client-protocol>=0.10` (official ACP SDK), lovelaice `agent/` engine (`lingo`-backed ReAct loop), `uv`, pytest.

## Global Constraints

- Legacy `lovelaice.acp.server.AcpServer` stays **byte-compatible** — do not edit it. warden pins `lovelaice>=2.6.0,<3.0` and must keep working untouched.
- Official ACP protocol version is the integer **`1`** (`acp.PROTOCOL_VERSION == 1`).
- lovelaice `StopReason` is a `str`-Enum mirroring ACP values (`END_TURN="end_turn"`, `MAX_TOKENS="max_tokens"`, `CANCELLED="cancelled"`); `stop.value` maps straight to ACP `PromptResponse.stop_reason`.
- Secrets: aegis reads the API key from an `api_key_file` path at spawn → env. **Never inline a key** in config or code.
- lovelaice ships this as **2.7.0** (minor, additive). Repoint the `lovelaice-acp` script; warden spawns the *class*, not the script, so this is safe.
- VS1 reuses the existing `create_coding_agent` factory (read + bash tools). The full toolset (write/edit/glob/list) and per-session MCP attach are later slices — out of scope here.
- Tests: `uv run python -m pytest`. lovelaice hermetic mode is `LOVELAICE_FAKE_LLM=1`. aegis live tests auto-skip when `lovelaice-acp` is off PATH.
- TDD: failing test first, minimal impl, commit per logical unit.

---

## File Structure

**lovelaice (`repos/lovelaice/`):**
- Create `src/lovelaice/acp/v1/__init__.py` — package marker.
- Create `src/lovelaice/acp/v1/server.py` — `AcpServerV1(acp.Agent)`: initialize / new_session / prompt / cancel + event translation. (load_session/ext-methods = VS4.)
- Create `src/lovelaice/acp/v1/__main__.py` — v1 stdio entrypoint (`main()` → `acp.run_agent`), env-driven default factory reusing `create_coding_agent`.
- Modify `pyproject.toml` — repoint `lovelaice-acp` script to `lovelaice.acp.v1.__main__:main`; bump version to `2.7.0`.
- Create `tests/acp/v1/test_server_v1.py` — hermetic unit tests (fake conn + FAKE_LLM).
- Create `tests/acp/v1/test_v1_stdio_live.py` — subprocess round-trip via the official SDK client (FAKE_LLM; no network).

**aegis (`repos/aegis/`):**
- Modify `src/aegis/config/__init__.py` — add `Lovelaice(_ProviderBase)`, register in `_PROVIDERS_BY_NAME` + `Provider` union.
- Modify `src/aegis/drivers/acp.py` — add `extra_env` seam on `AcpSession` + `AcpDriver.session()`.
- Create `src/aegis/drivers/lovelaice.py` — `LovelaiceDriver(AcpDriver)`.
- Modify `src/aegis/drivers/__init__.py` — register `"lovelaice": LovelaiceDriver`.
- Modify `pyproject.toml` — add `lovelaice>=2.7,<3` dependency.
- Create `tests/test_lovelaice_driver.py` — provider config + driver argv/env unit tests.
- Create `tests/test_lovelaice_live.py` — live round-trip, auto-skips when `lovelaice-acp` off PATH.

---

## Task 1: v1 server — `initialize`

**Files:**
- Create: `src/lovelaice/acp/v1/__init__.py`
- Create: `src/lovelaice/acp/v1/server.py`
- Test: `tests/acp/v1/test_server_v1.py`

**Interfaces:**
- Consumes: `acp.Agent`, `acp.PROTOCOL_VERSION`, `acp.InitializeResponse`, `acp.schema.AgentCapabilities`, `acp.schema.PromptCapabilities`.
- Produces: `AcpServerV1(agent_factory: Callable[..., Agent], conversation_store=None)` with async `initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw) -> InitializeResponse`. Constructor mirrors legacy `AcpServer` so hosts (warden) can migrate by import + dialect only.

- [ ] **Step 1: Write the failing test**

```python
# tests/acp/v1/test_server_v1.py
import pytest
import acp
from lovelaice.acp.v1.server import AcpServerV1


def _factory(**kw):
    raise AssertionError("factory not needed for initialize")


@pytest.mark.asyncio
async def test_initialize_advertises_protocol_v1():
    server = AcpServerV1(agent_factory=_factory)
    resp = await server.initialize(protocol_version=1)
    assert isinstance(resp, acp.InitializeResponse)
    assert resp.protocol_version == 1
    # load_session is a VS4 capability — advertised False for now.
    assert resp.agent_capabilities.load_session is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_initialize_advertises_protocol_v1 -v`
Expected: FAIL — `ModuleNotFoundError: lovelaice.acp.v1`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lovelaice/acp/v1/__init__.py
```

```python
# src/lovelaice/acp/v1/server.py
"""ACP v1 server on the official agent-client-protocol SDK.

Clean-room replacement for the legacy hand-rolled lovelaice.acp.server
("0.1" flat dialect), which stays frozen for warden. Implements the
acp.Agent interface; served over stdio via acp.run_agent (see __main__).
"""
from __future__ import annotations

from typing import Any, Callable

import acp
from acp.schema import AgentCapabilities, PromptCapabilities


class AcpServerV1(acp.Agent):
    """ACP-v1 agent. `agent_factory(conversation=None)` builds a lovelaice
    Agent per session — same contract as the legacy AcpServer so hosts
    that wire their own tools migrate by import + dialect only."""

    def __init__(self, *, agent_factory: Callable[..., Any],
                 conversation_store: Any = None) -> None:
        self._agent_factory = agent_factory
        self._store = conversation_store
        self._conn: acp.Client | None = None
        self._sessions: dict[str, Any] = {}

    def on_connect(self, conn: acp.Client) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int,
                         client_capabilities=None, client_info=None,
                         **kw: Any) -> acp.InitializeResponse:
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=False,
                prompt_capabilities=PromptCapabilities(
                    image=False, audio=False, embedded_context=False,
                ),
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_initialize_advertises_protocol_v1 -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd repos/lovelaice
git add src/lovelaice/acp/v1/__init__.py src/lovelaice/acp/v1/server.py tests/acp/v1/test_server_v1.py
git commit -m "feat(acp-v1): AcpServerV1 skeleton + initialize on official SDK"
```

---

## Task 2: v1 server — `new_session` builds + subscribes an agent

**Files:**
- Modify: `src/lovelaice/acp/v1/server.py`
- Test: `tests/acp/v1/test_server_v1.py`

**Interfaces:**
- Consumes: `acp.NewSessionResponse`; lovelaice `Agent.subscribe(fn)`.
- Produces: async `new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw) -> NewSessionResponse`; maps `session_id -> agent`; registers `self._emit` as the agent's event subscriber. `mcp_servers` accepted and ignored in VS1 (attach is VS2).

- [ ] **Step 1: Write the failing test**

```python
# tests/acp/v1/test_server_v1.py  (append)
class _FakeAgent:
    def __init__(self):
        self.subscribers = []
    def subscribe(self, fn):
        self.subscribers.append(fn)


@pytest.mark.asyncio
async def test_new_session_registers_agent_and_subscribes():
    made = _FakeAgent()
    server = AcpServerV1(agent_factory=lambda **kw: made)
    resp = await server.new_session(cwd="/tmp")
    assert isinstance(resp, acp.NewSessionResponse)
    sid = resp.session_id
    assert sid and server._sessions[sid] is made
    assert made.subscribers, "agent should have an event subscriber wired"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_new_session_registers_agent_and_subscribes -v`
Expected: FAIL — `AttributeError: 'AcpServerV1' object has no attribute 'new_session'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lovelaice/acp/v1/server.py — add imports + methods
import uuid
# ...
    async def new_session(self, cwd: str, additional_directories=None,
                          mcp_servers=None, **kw: Any) -> acp.NewSessionResponse:
        agent = self._agent_factory()
        sid = uuid.uuid4().hex[:16]
        agent.subscribe(lambda ev, _sid=sid: self._emit(_sid, ev))
        self._sessions[sid] = agent
        # mcp_servers accepted; per-session attach is a later slice.
        return acp.NewSessionResponse(session_id=sid)

    def _emit(self, session_id: str, ev: Any) -> None:
        # Filled in Task 3.
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_new_session_registers_agent_and_subscribes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd repos/lovelaice
git add src/lovelaice/acp/v1/server.py tests/acp/v1/test_server_v1.py
git commit -m "feat(acp-v1): new_session builds agent + wires event subscriber"
```

---

## Task 3: v1 server — event translation to `session/update`

**Files:**
- Modify: `src/lovelaice/acp/v1/server.py`
- Test: `tests/acp/v1/test_server_v1.py`

**Interfaces:**
- Consumes: lovelaice events `AssistantMessageFinalized(message)`, `ToolExecutionStart(call_id, name, args)`, `ToolExecutionEnd(call_id, result, is_error)`; SDK builders `acp.update_agent_message_text(text)`, `acp.start_tool_call(tool_call_id, title, kind=?, raw_input=?)`, `acp.update_tool_call(tool_call_id, status=?, content=?)`; `acp.tool_content(...)`; the connection's `conn.session_update(session_id, update)`.
- Produces: `_emit(session_id, ev)` schedules `conn.session_update(...)` on the running loop for each translatable event.

The agent emits events synchronously from `_emit`, but `conn.session_update` is async. Schedule it with `asyncio.ensure_future` on the loop captured at `prompt` time.

- [ ] **Step 1: Write the failing test**

```python
# tests/acp/v1/test_server_v1.py  (append)
import asyncio
from lovelaice.agent.events import (
    AssistantMessageFinalized, ToolExecutionStart, ToolExecutionEnd,
)


class _FakeConn:
    def __init__(self):
        self.updates = []
    async def session_update(self, session_id, update, **kw):
        self.updates.append((session_id, update))


class _Msg:
    content = "hello from agent"


@pytest.mark.asyncio
async def test_emit_translates_message_and_tool_events():
    conn = _FakeConn()
    server = AcpServerV1(agent_factory=lambda **kw: _FakeAgent())
    server.on_connect(conn)
    server._loop = asyncio.get_running_loop()

    server._emit("s1", AssistantMessageFinalized(message=_Msg()))
    server._emit("s1", ToolExecutionStart(call_id="c1", name="read", args={"path": "x"}))
    server._emit("s1", ToolExecutionEnd(call_id="c1", result=None, is_error=False))
    await asyncio.sleep(0.05)  # let scheduled coros run

    kinds = [type(u).__name__ for _sid, u in conn.updates]
    assert "AgentMessageChunk" in kinds
    assert "ToolCallStart" in kinds
    assert "ToolCallProgress" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_emit_translates_message_and_tool_events -v`
Expected: FAIL — no updates captured (`_emit` is a no-op).

- [ ] **Step 3: Write minimal implementation**

```python
# src/lovelaice/acp/v1/server.py
import asyncio
from lovelaice.agent.events import (
    AssistantMessageFinalized, ToolExecutionStart, ToolExecutionEnd,
)
# ... in __init__ add: self._loop: asyncio.AbstractEventLoop | None = None

    def _emit(self, session_id: str, ev: Any) -> None:
        update = self._translate(ev)
        if update is None or self._conn is None:
            return
        loop = self._loop or asyncio.get_event_loop()
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                self._conn.session_update(session_id=session_id, update=update),
                loop=loop,
            )
        )

    def _translate(self, ev: Any):
        if isinstance(ev, AssistantMessageFinalized):
            text = ev.message.content if isinstance(ev.message.content, str) else ""
            return acp.update_agent_message_text(text) if text else None
        if isinstance(ev, ToolExecutionStart):
            return acp.start_tool_call(
                tool_call_id=ev.call_id, title=ev.name,
                kind="other", raw_input=ev.args)
        if isinstance(ev, ToolExecutionEnd):
            status = "failed" if ev.is_error else "completed"
            text = ""
            content = getattr(ev.result, "content", None) or []
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "")
            return acp.update_tool_call(
                tool_call_id=ev.call_id, status=status,
                content=[acp.tool_content(acp.text_block(text))] if text else None)
        return None
```

Note: verify `acp.tool_content`'s exact call shape against the SDK at implementation time (`uv run python -c "import acp,inspect;print(inspect.signature(acp.tool_content))"`); if it wraps a content block, the call above is correct; adjust the single argument if the signature differs.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_emit_translates_message_and_tool_events -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd repos/lovelaice
git add src/lovelaice/acp/v1/server.py tests/acp/v1/test_server_v1.py
git commit -m "feat(acp-v1): translate agent events to session/update via SDK builders"
```

---

## Task 4: v1 server — `prompt` + `cancel`

**Files:**
- Modify: `src/lovelaice/acp/v1/server.py`
- Test: `tests/acp/v1/test_server_v1.py`

**Interfaces:**
- Consumes: lovelaice `agent.prompt(text) -> StopReason` (async); `acp.PromptResponse`.
- Produces: async `prompt(self, prompt, session_id, message_id=None, **kw) -> PromptResponse` (captures the running loop, runs the agent turn, returns `stop_reason=stop.value`); async `cancel(self, session_id, **kw) -> None` (cancels the in-flight task).

- [ ] **Step 1: Write the failing test** (uses the real engine with a fake LLM)

```python
# tests/acp/v1/test_server_v1.py  (append)
import os
from pathlib import Path
from lovelaice.agent import Agent, AgentConfig
from lovelaice.agent.loops.react_native import ReActNative


def _real_agent_factory(tmp_path):
    os.environ["LOVELAICE_FAKE_LLM"] = "1"
    from unittest.mock import AsyncMock
    from lingo.llm import Message
    import lovelaice.agent.agent as agent_mod
    fake = AsyncMock()
    fake.chat = AsyncMock(
        return_value=Message.assistant("done", stop_reason="stop"))
    agent_mod._build_llm = lambda cfg: fake
    def factory(**kw):
        cfg = AgentConfig(model="fake/model", cwd=str(tmp_path))
        return Agent(config=cfg, tools=[], loop=ReActNative(),
                     session_path=tmp_path / "s.jsonl")
    return factory


@pytest.mark.asyncio
async def test_prompt_returns_stop_reason(tmp_path):
    server = AcpServerV1(agent_factory=_real_agent_factory(tmp_path))
    server.on_connect(_FakeConn())
    new = await server.new_session(cwd=str(tmp_path))
    resp = await server.prompt(
        prompt=[{"type": "text", "text": "hi"}], session_id=new.session_id)
    assert isinstance(resp, acp.PromptResponse)
    assert resp.stop_reason == "end_turn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_prompt_returns_stop_reason -v`
Expected: FAIL — `AttributeError: 'AcpServerV1' object has no attribute 'prompt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lovelaice/acp/v1/server.py
    async def prompt(self, prompt, session_id: str, message_id=None,
                     **kw: Any) -> acp.PromptResponse:
        agent = self._sessions.get(session_id)
        if agent is None:
            raise acp.RequestError(
                code=-32602, message=f"unknown sessionId: {session_id}")
        self._loop = asyncio.get_running_loop()
        text = "".join(b.get("text", "") for b in prompt
                       if isinstance(b, dict) and b.get("type") == "text")
        task = asyncio.ensure_future(agent.prompt(text))
        self._inflight = task
        try:
            stop = await task
        except asyncio.CancelledError:
            return acp.PromptResponse(stop_reason="cancelled")
        finally:
            self._inflight = None
        value = getattr(stop, "value", None) or str(stop)
        return acp.PromptResponse(stop_reason=value)

    async def cancel(self, session_id: str, **kw: Any) -> None:
        task = getattr(self, "_inflight", None)
        if task is not None and not task.done():
            task.cancel()
```

(Add `self._inflight = None` to `__init__`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
cd repos/lovelaice
git add src/lovelaice/acp/v1/server.py tests/acp/v1/test_server_v1.py
git commit -m "feat(acp-v1): prompt returns StopReason; cancel aborts in-flight turn"
```

---

## Task 5: v1 stdio entrypoint + repoint `lovelaice-acp` + version bump

**Files:**
- Create: `src/lovelaice/acp/v1/__main__.py`
- Modify: `pyproject.toml` (script target + version)
- Test: `tests/acp/v1/test_v1_stdio_live.py`

**Interfaces:**
- Consumes: `acp.run_agent(agent, input_stream, output_stream)`; `lovelaice.coding.host.create_coding_agent`; the official SDK **client** (`acp.connect_to_agent`, `acp.Client`) for the round-trip test.
- Produces: `lovelaice.acp.v1.__main__:main`; the `lovelaice-acp` console script now runs the v1 server.

- [ ] **Step 1: Write the failing test** (subprocess round-trip through the real SDK client)

```python
# tests/acp/v1/test_v1_stdio_live.py
import asyncio
import os
import sys
import pytest
import acp


class _Client(acp.Client):
    def __init__(self):
        self.messages = []
    def on_connect(self, conn):
        return None
    async def session_update(self, session_id, update, **kw):
        if type(update).__name__ == "AgentMessageChunk":
            self.messages.append(getattr(update.content, "text", ""))
    async def request_permission(self, options, session_id, tool_call, **kw):
        return acp.RequestPermissionResponse(
            outcome={"outcome": "selected", "optionId": options[0].option_id})
    async def read_text_file(self, path, session_id, limit=None, line=None, **kw):
        return acp.ReadTextFileResponse(content="")
    async def write_text_file(self, content, path, session_id, **kw):
        return None
    async def create_terminal(self, *a, **kw): return None
    async def terminal_output(self, *a, **kw): return None
    async def wait_for_terminal_exit(self, *a, **kw): return None
    async def kill_terminal(self, *a, **kw): return None
    async def release_terminal(self, *a, **kw): return None
    async def ext_method(self, method, params): return {}
    async def ext_notification(self, method, params): return None


@pytest.mark.asyncio
async def test_v1_server_handshakes_with_official_sdk_client():
    env = dict(os.environ, LOVELAICE_FAKE_LLM="1", LOVELAICE_MODEL="fake/model")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "lovelaice.acp.v1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, limit=16 * 1024 * 1024)
    client = _Client()
    conn = acp.connect_to_agent(client, proc.stdin, proc.stdout)
    try:
        init = await conn.initialize(
            protocol_version=1,
            client_capabilities={"fs": {"readTextFile": True, "writeTextFile": True}},
            client_info={"name": "test", "version": "0"})
        assert init.protocol_version == 1
        sess = await conn.new_session(cwd=".", mcp_servers=[])
        resp = await conn.prompt(
            session_id=sess.session_id, prompt=[{"type": "text", "text": "hi"}])
        assert resp.stop_reason in ("end_turn", "cancelled")
    finally:
        proc.terminate()
        await proc.wait()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_v1_stdio_live.py -v`
Expected: FAIL — `No module named lovelaice.acp.v1.__main__`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/lovelaice/acp/v1/__main__.py
"""`python -m lovelaice.acp.v1` (and the `lovelaice-acp` script) run the
ACP v1 stdio server. Config from env: LOVELAICE_MODEL / LOVELAICE_BASE_URL /
OPENROUTER_API_KEY|OPENAI_API_KEY / LOVELAICE_CWD / LOVELAICE_SESSION_PATH.
LOVELAICE_FAKE_LLM=1 swaps a canned LLM for tests."""
import asyncio
import os
from pathlib import Path

import acp

from lovelaice.acp.v1.server import AcpServerV1
from lovelaice.coding.host import create_coding_agent


def _default_factory(**_kw):
    if os.getenv("LOVELAICE_FAKE_LLM"):
        from unittest.mock import AsyncMock
        from lingo.llm import Message
        import lovelaice.agent.agent as agent_mod
        fake = AsyncMock()
        fake.chat = AsyncMock(
            return_value=Message.assistant("ok", stop_reason="stop"))
        agent_mod._build_llm = lambda cfg: fake
    session_path = Path(os.getenv(
        "LOVELAICE_SESSION_PATH",
        str(Path.home() / ".lovelaice" / "sessions" / "ad-hoc.jsonl")))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return create_coding_agent(
        model=os.getenv("LOVELAICE_MODEL", "anthropic/claude-haiku-4-5"),
        session_path=session_path,
        cwd=os.getenv("LOVELAICE_CWD", os.getcwd()),
        base_url=os.getenv("LOVELAICE_BASE_URL"),
        api_key=os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY"),
    )


def main() -> None:
    server = AcpServerV1(agent_factory=_default_factory)
    asyncio.run(acp.run_agent(server))


if __name__ == "__main__":
    main()
```

Then in `pyproject.toml`, repoint the script and bump the version:

```toml
[project.scripts]
lovelaice = "lovelaice.cli:app"
lovelaice-acp = "lovelaice.acp.v1.__main__:main"
```

```toml
# [project] version bump
version = "2.7.0"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/lovelaice && uv pip install -e . && uv run python -m pytest tests/acp/v1/test_v1_stdio_live.py -v`
Expected: PASS. Then confirm the whole v1 suite + the untouched legacy suite are green:
Run: `uv run python -m pytest tests/acp -v`
Expected: PASS (legacy `AcpServer` tests unchanged).

- [ ] **Step 5: Commit**

```bash
cd repos/lovelaice
git add src/lovelaice/acp/v1/__main__.py pyproject.toml tests/acp/v1/test_v1_stdio_live.py
git commit -m "feat(acp-v1): stdio entrypoint via acp.run_agent; repoint lovelaice-acp; v2.7.0"
```

---

## Task 6: aegis — `Lovelaice` provider config

**Files:**
- Modify: `src/aegis/config/__init__.py:44-73`
- Test: `tests/test_lovelaice_driver.py`

**Interfaces:**
- Consumes: `_ProviderBase`, `Permission`.
- Produces: `Lovelaice(_ProviderBase)` with `name: Literal["lovelaice"]`, `base_url: str | None = None`, `api_key_file: str | None = None`, `permission: Permission = Permission.full`; added to the `Provider` union and `_PROVIDERS_BY_NAME`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lovelaice_driver.py
from aegis.config import Agent, Lovelaice


def test_lovelaice_provider_parses():
    a = Agent(provider=Lovelaice(model="qwen2.5:7b", base_url="http://localhost:11434/v1"))
    assert a.harness == "lovelaice"
    assert a.model == "qwen2.5:7b"
    assert a.provider.base_url == "http://localhost:11434/v1"


def test_lovelaice_flat_shape_resolves():
    a = Agent(harness="lovelaice", model="anthropic/claude-haiku-4-5")
    assert a.provider.name == "lovelaice"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py -v`
Expected: FAIL — `ImportError: cannot import name 'Lovelaice'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/config/__init__.py — after class OpenCode
class Lovelaice(_ProviderBase):
    name: Literal["lovelaice"] = "lovelaice"
    permission: Permission = Permission.full
    base_url: str | None = None
    api_key_file: str | None = None


# update the union + registry:
Provider = ClaudeCode | GeminiCLI | OpenCode | Lovelaice

_PROVIDERS_BY_NAME: dict[str, type[_ProviderBase]] = {
    "claude-code": ClaudeCode,
    "gemini":      GeminiCLI,
    "opencode":    OpenCode,
    "lovelaice":   Lovelaice,
}
```

In the flat-shape resolver `_sync_provider_and_flat`, the generic
`kw = {"model": ..., "permission": ...}` path already covers `Lovelaice`
(base_url/api_key_file default to None); no special-case needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd repos/aegis
git add src/aegis/config/__init__.py tests/test_lovelaice_driver.py
git commit -m "feat(config): Lovelaice provider (model + base_url + api_key_file)"
```

---

## Task 7: aegis — `extra_env` seam on the ACP session

**Files:**
- Modify: `src/aegis/drivers/acp.py` (`AcpSession.__init__`, `AcpSession.start`, `AcpDriver.session`)
- Test: `tests/test_lovelaice_driver.py`

**Interfaces:**
- Consumes: existing `AcpSession.__init__(agent, cwd, mcp_url, handle, *, resume_session_id=None)` and `AcpDriver.session(...)`.
- Produces: `AcpSession.__init__(..., extra_env: dict[str, str] | None = None)`; the env is merged into the subprocess environment in `start()`, composing with pre-spawn-hook env. `AcpDriver.extra_env(agent) -> dict[str, str]` hook (default `{}`) consumed by `session()`/`resume()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lovelaice_driver.py  (append)
from aegis.drivers.acp import AcpSession


def test_acp_session_accepts_extra_env():
    s = AcpSession(agent=None, cwd="/tmp", mcp_url="", handle="h",
                   extra_env={"LOVELAICE_MODEL": "x"})
    assert s._extra_env == {"LOVELAICE_MODEL": "x"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py::test_acp_session_accepts_extra_env -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'extra_env'`.

- [ ] **Step 3: Write minimal implementation**

In `AcpSession.__init__`, add the parameter and store it:

```python
    def __init__(self, agent: Agent, cwd: str,
                 mcp_url: str, handle: str,
                 *, resume_session_id: str | None = None,
                 extra_env: dict[str, str] | None = None) -> None:
        # ... existing assignments ...
        self._extra_env = dict(extra_env or {})
```

In `AcpSession.start()`, after `_apply_pre_spawn_hooks` computes `env`, merge:

```python
        argv, env = await self._apply_pre_spawn_hooks()
        if self._extra_env:
            env = {**(env if env is not None else os.environ), **self._extra_env}
```

In `AcpDriver`, add a hook and thread it through `session()` / `resume()`:

```python
    def extra_env(self, agent: Agent) -> dict[str, str]:
        return {}

    def session(self, agent, cwd, mcp_url, handle):
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle,
                             extra_env=self.extra_env(agent))
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s

    def resume(self, agent, cwd, mcp_url, handle, session_id):
        s = self.SESSION_CLS(agent, cwd, mcp_url, handle,
                             resume_session_id=session_id,
                             extra_env=self.extra_env(agent))
        s.BASE_CMD = self.build_argv(agent, cwd, mcp_url, handle)
        return s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py::test_acp_session_accepts_extra_env -v`
Expected: PASS. Then regression-check the existing ACP driver tests:
Run: `uv run python -m pytest tests/ -k acp -v`
Expected: PASS (Gemini/OpenCode drivers unaffected — `extra_env` defaults to `{}`).

- [ ] **Step 5: Commit**

```bash
cd repos/aegis
git add src/aegis/drivers/acp.py tests/test_lovelaice_driver.py
git commit -m "feat(drivers): extra_env seam on AcpSession/AcpDriver"
```

---

## Task 8: aegis — `LovelaiceDriver`

**Files:**
- Create: `src/aegis/drivers/lovelaice.py`
- Modify: `src/aegis/drivers/__init__.py`
- Test: `tests/test_lovelaice_driver.py`

**Interfaces:**
- Consumes: `AcpDriver`, `aegis.config.Agent`/`Lovelaice`.
- Produces: `LovelaiceDriver(AcpDriver)` with `BASE_CMD = ["lovelaice-acp"]` and `extra_env(agent)` mapping the provider's `model` → `LOVELAICE_MODEL`, `base_url` → `LOVELAICE_BASE_URL`, and (if `api_key_file` set and readable) its contents → `OPENROUTER_API_KEY`. Registered as `"lovelaice"` in `DRIVERS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lovelaice_driver.py  (append)
from pathlib import Path
from aegis.drivers import get_driver
from aegis.drivers.lovelaice import LovelaiceDriver
from aegis.config import Agent, Lovelaice


def test_driver_registered():
    assert isinstance(get_driver("lovelaice"), LovelaiceDriver)


def test_extra_env_maps_model_base_url_and_key(tmp_path):
    key = tmp_path / "or.token"
    key.write_text("sk-test-123\n")
    a = Agent(provider=Lovelaice(model="qwen2.5:7b",
                                 base_url="http://localhost:11434/v1",
                                 api_key_file=str(key)))
    env = LovelaiceDriver().extra_env(a)
    assert env["LOVELAICE_MODEL"] == "qwen2.5:7b"
    assert env["LOVELAICE_BASE_URL"] == "http://localhost:11434/v1"
    assert env["OPENROUTER_API_KEY"] == "sk-test-123"


def test_base_cmd():
    assert LovelaiceDriver().build_argv(
        Agent(harness="lovelaice", model="m"), ".", "", "h") == ["lovelaice-acp"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py -k "driver_registered or extra_env_maps or base_cmd" -v`
Expected: FAIL — `ModuleNotFoundError: aegis.drivers.lovelaice`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/drivers/lovelaice.py
"""Lovelaice driver — native harness-free agent over official ACP v1.

Spawns `lovelaice-acp` (lovelaice's ACP-v1 stdio server) and drives it
with the generic AcpDriver. Model / endpoint / key are injected as env at
spawn (lovelaice reads LOVELAICE_MODEL / LOVELAICE_BASE_URL /
OPENROUTER_API_KEY). Point base_url at a local endpoint for local models.
"""
from __future__ import annotations

from pathlib import Path

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class LovelaiceDriver(AcpDriver):
    BASE_CMD = ["lovelaice-acp"]

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        return list(self.BASE_CMD)

    def extra_env(self, agent: Agent) -> dict[str, str]:
        env: dict[str, str] = {}
        if getattr(agent, "model", ""):
            env["LOVELAICE_MODEL"] = agent.model
        provider = getattr(agent, "provider", None)
        base_url = getattr(provider, "base_url", None)
        if base_url:
            env["LOVELAICE_BASE_URL"] = base_url
        key_file = getattr(provider, "api_key_file", None)
        if key_file:
            p = Path(key_file).expanduser()
            if p.is_file():
                env["OPENROUTER_API_KEY"] = p.read_text().strip()
        return env
```

```python
# src/aegis/drivers/__init__.py — register
from aegis.drivers.lovelaice import LovelaiceDriver
# ...
DRIVERS: dict[str, type[HarnessDriver]] = {
    "claude-code": ClaudeDriver,
    "gemini":      GeminiDriver,
    "opencode":    OpenCodeDriver,
    "lovelaice":   LovelaiceDriver,
}
# add "LovelaiceDriver" to __all__
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_driver.py -v`
Expected: PASS (all provider + driver tests).

- [ ] **Step 5: Commit**

```bash
cd repos/aegis
git add src/aegis/drivers/lovelaice.py src/aegis/drivers/__init__.py tests/test_lovelaice_driver.py
git commit -m "feat(drivers): LovelaiceDriver — native ACP-v1 agent + env plumbing"
```

---

## Task 9: aegis — depend on lovelaice + live end-to-end round-trip

**Files:**
- Modify: `pyproject.toml` (add dependency)
- Create: `tests/test_lovelaice_live.py`

**Interfaces:**
- Consumes: `LovelaiceDriver`, `aegis.config.Agent`/`Lovelaice`, the aegis MCP runtime (`mcp_url` may be `""` for VS1 since MCP attach is VS2).
- Produces: a live round-trip that spawns `lovelaice-acp` via the driver and asserts a message comes back; auto-skips when the CLI is off PATH.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lovelaice_live.py
import os
import shutil
import pytest
from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

pytestmark = pytest.mark.skipif(
    shutil.which("lovelaice-acp") is None, reason="lovelaice-acp not on PATH")


@pytest.mark.asyncio
async def test_lovelaice_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOVELAICE_FAKE_LLM", "1")
    agent = Agent(provider=Lovelaice(model="fake/model"))
    driver = LovelaiceDriver()
    sess = driver.session(agent, str(tmp_path), "", "handle")
    await sess.start()
    await sess.send("hello")
    kinds = [type(ev).__name__ async for ev in sess.events()]
    await sess.close()
    assert "Result" in kinds  # terminal event from AcpSession.send
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_live.py -v`
Expected: FAIL or SKIP — SKIP until `lovelaice-acp` is installed into aegis's env (next step). After install it should run and (initially) fail if plumbing is off.

- [ ] **Step 3: Add the dependency + install**

```toml
# repos/aegis/pyproject.toml — dependencies
    "lovelaice>=2.7,<3",
```

Install into aegis's env (lovelaice is a local sibling; use an editable path dep during development, or the published floor once 2.7.0 is released):

Run: `cd repos/aegis && uv pip install -e ../lovelaice && uv pip install -e .`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd repos/aegis && uv run python -m pytest tests/test_lovelaice_live.py -v`
Expected: PASS — a native lovelaice agent completed a turn end-to-end through the driver.

- [ ] **Step 5: Commit**

```bash
cd repos/aegis
git add pyproject.toml tests/test_lovelaice_live.py
git commit -m "feat: ship lovelaice as an aegis dependency; live native-agent round-trip"
```

---

## Task 10: Manual probe in a real aegis tab

**Files:** none (manual verification, mirrors `.playground/acp-probe/FINDINGS.md`).

- [ ] **Step 1: Add a `lovelaice` agent to a scratch `.aegis.yaml`** OUTSIDE the Workspace (per the isolate-test-projects rule), e.g. `/tmp/lovel-probe/.aegis.yaml`:

```yaml
agents:
  local:
    provider: lovelaice
    model: anthropic/claude-haiku-4-5
    api_key_file: /home/apiad/Workspace/.claude/openrouter.token
default_agent: local
```

- [ ] **Step 2: Launch aegis there, open a tab, ask it to read a file.**

Run: `cd /tmp/lovel-probe && (cd /home/apiad/Workspace/repos/aegis && uv run aegis --cwd /tmp/lovel-probe)`
Expected: the agent reads the file and answers; the transcript shows a `read` tool call rendered with its path hint, then the assistant message. Metrics may show 0/0 (usage surfacing is VS4).

- [ ] **Step 3: Record findings** in `repos/aegis/.playground/lovelaice-probe/FINDINGS.md` (gotchas, any signature drift vs this plan) so VS2–VS4 plans start from ground truth.

---

## Subsequent slices (each becomes its own plan, authored against VS1's real module)

These are intentionally **not** expanded into TDD steps here: their exact code
binds to symbols VS1 creates (`AcpServerV1`, its `_emit`/`_translate`, the
entrypoint factory). Writing detailed steps now would reference not-yet-existing
signatures. After VS1 lands and `FINDINGS.md` is recorded, run
`superpowers:writing-plans` for each.

- **VS2 — per-session MCP attach (first-class MCP).** Wire `AcpServerV1.new_session`
  to read typed `mcp_servers` (`acp.schema.HttpMcpServer` with `.headers` as
  `[{name,value}]`, plus stdio), translate to `lovelaice.mcp.connect()` shape,
  connect each on a managed background session, wrap via `_wrap_mcp_tool`, attach
  to the session's agent, tear down on close. Upstream the HTTP-on-a-thread
  lifecycle into `lovelaice.mcp` (reference: warden `_acp_driver._start_http_mcp`).
  Prove the native agent calling `aegis_claim` live. Files:
  `lovelaice/acp/v1/server.py`, `lovelaice/mcp.py`; aegis passes its real
  `mcp_url` (not `""`) in the live test.
- **VS3 — full native toolset.** Add `write`/`edit`/`glob`/`list` to
  `create_coding_agent` (reuse `tools/files` + `tools/search`), coding-host
  wrappers for parent-dir creation / unambiguous-edit / output caps, ACP `kind`s,
  `path_guard` coverage, updated `CODING_PREAMBLE`. Files:
  `lovelaice/coding/tools/*`, `lovelaice/coding/host.py`.
- **VS4 — parity/polish.** `load_session` (map ACP `sessionId` ⟷ lovelaice
  `conversationId`; flip advertised `load_session=True`); `usage` in
  `PromptResponse` (surface lingo token counts → aegis metrics); streaming
  message chunks; `workflow/run` + `conversation/archive` as ACP `ext_method`/
  `ext_notification`. Then author the **warden upgrade checklist** know-how doc.

---

## Self-Review

**Spec coverage (VS1 scope):** ✅ v1 server on official SDK (T1–T5); repoint entrypoint (T5); legacy frozen (Global Constraints + T5 verifies legacy suite green); `Lovelaice` provider (T6); `extra_env` seam (T7); `LovelaiceDriver` + registry (T8); aegis ships lovelaice (T9); end-to-end native agent (T9–T10). Parts 1b/2/toolset/load_session/usage/ext-methods are explicitly deferred to VS2–VS4 with named files/interfaces.

**Placeholder scan:** No TBD/TODO/"handle edge cases". One flagged verification (T3, `acp.tool_content` signature) with the exact command to confirm — not a placeholder, a guard against SDK-minor drift.

**Type consistency:** `AcpServerV1(agent_factory=…, conversation_store=…)` constructor consistent T1–T5. `extra_env(agent) -> dict[str,str]` consistent T7 (definition) → T8 (override). `Lovelaice` fields (`model`, `base_url`, `api_key_file`) consistent T6 → T8. `stop.value` → `PromptResponse.stop_reason` matches the real `StopReason` enum values.
