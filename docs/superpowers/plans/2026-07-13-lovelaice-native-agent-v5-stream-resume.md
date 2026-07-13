# Native lovelaice agent (VS5 — streaming + load_session) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** (A) The ACP v1 server streams assistant text token-by-token; (B) it supports `load_session` so an ACP client (aegis) can resume a conversation with restored context. `workflow/run`/`archive` ext-methods stay deferred until warden migrates.

**Architecture:**
- **Streaming:** lingo `LLM(on_token=…)` fires per content token. Wire it in `Harness.__init__` to emit a new `AssistantMessageDelta` event. The v1 server translates deltas → ACP `AgentMessageChunk`; on `AssistantMessageFinalized` it accumulates usage and emits the full content only as a fallback when nothing streamed. Legacy `AcpServer` ignores the new event (unchanged). CLI ignores it too (still renders finalized).
- **Resume:** `Agent` already restores context via `Session.load(path)` when the session jsonl exists. Give each ACP session a **deterministic per-session-id jsonl path**; `new_session` builds the agent on it, `load_session(session_id)` rebuilds on the same path (works across subprocess restarts). Advertise `load_session=True`.

## Global Constraints
- No changes to the legacy `AcpServer`; the new `AssistantMessageDelta` event is additive (unknown events are ignored by existing consumers).
- Factory contract widens to `agent_factory(*, mcp_tools=None, session_path=None)`; existing `**kw` factories absorb it.
- Per-session jsonl dir: `LOVELAICE_SESSIONS_DIR` env or `~/.lovelaice/acp-sessions/`; filename `<session_id>.jsonl`.
- Ship lovelaice **2.11.0**; aegis floor → `>=2.11,<3`.
- Real-model probes for both before release. Tests inline.

## Files
- `src/lovelaice/agent/events.py` — add `AssistantMessageDelta`.
- `src/lovelaice/agent/harness.py` — wire `llm._on_token` → emit delta.
- `src/lovelaice/acp/v1/server.py` — delta→chunk; fallback content emit; `initialize` load_session=True; `_session_path_for`; new_session per-sid path; `load_session`.
- `src/lovelaice/acp/v1/__main__.py` — `_default_factory(*, mcp_tools=None, session_path=None)`.
- Tests: `tests/agent/test_streaming_delta.py`, extend `tests/acp/v1/test_server_v1.py`; aegis `tests/test_lovelaice_resume_live.py`.

## Task 1 (A): AssistantMessageDelta + harness wiring

**Interfaces:** `AssistantMessageDelta(text: str)` dataclass event. `Harness.__init__` sets `self.llm._on_token = self._emit_token_delta` (guarded), where `_emit_token_delta(tok)` calls `self.emit(AssistantMessageDelta(text=tok))`.

- [ ] **Step 1: Failing test**
```python
# tests/agent/test_streaming_delta.py
import pytest
from lovelaice.agent.events import AssistantMessageDelta


def test_harness_emits_delta_on_token():
    from lovelaice.agent.harness import Harness
    from lovelaice.agent.tools import ToolRegistry
    from lovelaice.agent.hooks import HookRegistry

    class FakeLLM:
        _on_token = None

    h = Harness(llm=FakeLLM(), tools=ToolRegistry(), hooks=HookRegistry(),
                system_prompt="s")
    seen = []
    h.subscribe(lambda ev: seen.append(ev) if isinstance(ev, AssistantMessageDelta) else None)
    # lingo would call this per token; simulate:
    h.llm._on_token("hel")
    h.llm._on_token("lo")
    assert [e.text for e in seen] == ["hel", "lo"]
```

- [ ] **Step 2: Run → fail.** `cd repos/lovelaice && uv run python -m pytest tests/agent/test_streaming_delta.py -v`
- [ ] **Step 3: Implement:**
  - events.py: `@dataclass class AssistantMessageDelta(AgentEvent): text: str`.
  - harness.py `__init__` after `self.llm = llm`:
    ```python
    try:
        self.llm._on_token = self._emit_token_delta
    except Exception:  # noqa: BLE001 — non-lingo/mocked llm
        pass
    ```
    and method:
    ```python
    def _emit_token_delta(self, token: str) -> None:
        from lovelaice.agent.events import AssistantMessageDelta
        self.emit(AssistantMessageDelta(text=token))
    ```
- [ ] **Step 4: Run → pass** + full suite green.
- [ ] **Step 5: Commit** — `feat(agent): AssistantMessageDelta streamed from lingo on_token`

## Task 2 (A): v1 server streams deltas as AgentMessageChunk

**Interfaces:** `_translate(AssistantMessageDelta)` → `acp.update_agent_message_text(text)`. Per-turn `self._streamed_any` (reset in `prompt`); set True when a delta is emitted. On `AssistantMessageFinalized`: accumulate usage (existing) and emit content chunk **only if** `not self._streamed_any`.

- [ ] **Step 1: Failing test** — append to `tests/acp/v1/test_server_v1.py`:
```python
@pytest.mark.asyncio
async def test_deltas_stream_and_finalized_not_duplicated():
    from lovelaice.agent.events import AssistantMessageDelta, AssistantMessageFinalized
    conn = _FakeConn()
    server = AcpServerV1(agent_factory=lambda **kw: _FakeAgent())
    server.on_connect(conn); server._loop = asyncio.get_running_loop()
    server._streamed_any = False
    server._emit("s", AssistantMessageDelta(text="he"))
    server._emit("s", AssistantMessageDelta(text="llo"))
    server._emit("s", AssistantMessageFinalized(message=_Msg()))  # _Msg.content="hello from agent"
    await asyncio.sleep(0.05)
    texts = [getattr(u, "content", None).text for _s, u in conn.updates
             if type(u).__name__ == "AgentMessageChunk"]
    assert texts == ["he", "llo"]  # finalized did NOT re-emit content
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** in `_emit`/`_translate` + `prompt` reset. In `_emit`: on delta set `self._streamed_any = True`; on finalized accumulate usage then translate—but gate the finalized content emit on `not self._streamed_any`. Simplest: handle finalized inline in `_emit` (usage always; content chunk only if not streamed) and let `_translate` handle delta; drop finalized from `_translate`.
- [ ] **Step 4: Run → pass** + full v1 suite.
- [ ] **Step 5: Commit** — `feat(acp-v1): stream assistant deltas; finalized content only as fallback`

## Task 3 (B): load_session + per-sid session path

**Interfaces:** `_default_factory(*, mcp_tools=None, session_path=None)` uses `session_path` when given. `AcpServerV1._session_path_for(sid) -> str`. `new_session` generates sid first, passes `session_path=self._session_path_for(sid)`. `load_session(cwd, session_id, mcp_servers=None, **kw)` builds an agent on that path, subscribes, stores, returns `acp.LoadSessionResponse()`. `initialize` advertises `load_session=True`.

- [ ] **Step 1: Failing tests** — update `test_initialize_advertises_protocol_v1` to assert `load_session is True`; add:
```python
@pytest.mark.asyncio
async def test_load_session_rebuilds_on_same_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOVELAICE_SESSIONS_DIR", str(tmp_path))
    paths = []
    def factory(*, mcp_tools=None, session_path=None, **kw):
        paths.append(session_path); return _FakeAgent()
    server = AcpServerV1(agent_factory=factory)
    new = await server.new_session(cwd=str(tmp_path))
    await server.load_session(cwd=str(tmp_path), session_id=new.session_id)
    assert paths[0] is not None and paths[0] == paths[1]  # same deterministic path
    assert str(new.session_id) in paths[0]
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement:** `_session_path_for`, reorder new_session (sid first), pass session_path; `load_session` mirrors new_session (build+subscribe+store, reuse `_mcp_specs_from_acp`/`build_agent_tools`); `initialize` load_session=True; default factory honors session_path (fallback to `LOVELAICE_SESSION_PATH` env / default).
- [ ] **Step 4: Run → pass** + full suite.
- [ ] **Step 5: Commit** — `feat(acp-v1): load_session with deterministic per-session jsonl path`

## Task 4: real-model probes + release 2.11.0
- [ ] **Streaming probe** (aegis, local editable, `--no-sync`): a real turn yields **multiple** `AssistantText` events mid-turn (not one blob).
- [ ] **Resume probe** (aegis driver): session1 `start`→send "Remember the codeword is BANANA."→`close`; session2 = `driver.resume(session_id=sid1)` (fresh subprocess)→send "What is the codeword?"→answer contains "BANANA".
- [ ] Bump `2.11.0` + CHANGELOG; full suite; commit, push, `gh release create v2.11.0`; poll PyPI==2.11.0.

## Task 5: aegis bump to 2.11
- [ ] aegis `pyproject.toml` → `lovelaice>=2.11,<3`; `uv lock --refresh --upgrade-package lovelaice` (own step, check rc); `uv sync` (own step, check rc).
- [ ] Re-run `tests/test_lovelaice_{live,mcp_live,resume_live}.py` green. Commit + push.

## Self-Review
**Coverage:** streaming (T1–T2), resume (T3), proven with real models (T4), consumed by aegis (T5). ext-methods deferred (documented). **Type consistency:** `AssistantMessageDelta.text` (T1) → `_translate` (T2); factory `session_path` kwarg (T3) consistent across `_default_factory` + server calls; `_streamed_any` reset in `prompt`, set in `_emit` (T2).
