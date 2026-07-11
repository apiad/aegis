# Native lovelaice agent (VS2 — per-session MCP attach) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A native lovelaice agent driven by aegis can call the aegis MCP plane (`aegis_claim`, `aegis_enqueue`, …) — the v1 server reads ACP `new_session.mcp_servers`, connects each (HTTP + stdio) with a managed lifecycle, wraps their tools, and builds the session's agent with them.

**Architecture:** Extend `lovelaice.mcp` with a first-class managed background session that supports **HTTP and stdio** with explicit teardown (upstreaming warden's `_start_http_mcp`). `AcpServerV1.new_session` connects the ACP-supplied `mcp_servers`, wraps their tools as `AgentTool`s, and passes them to the agent factory so they're in the registry at construction (system prompt includes them). Track sessions per ACP `session_id`; tear down on `cancel`/close.

**Tech Stack:** Python 3.13, `agent-client-protocol` SDK (typed `HttpMcpServer`/`McpServerStdio` on `new_session`), `mcp` SDK client, lovelaice `agent/` engine.

## Global Constraints

- Legacy `lovelaice.acp.server.AcpServer` stays byte-compatible (warden). Do not edit it.
- The default `lovelaice-acp` factory owns the coding toolset; MCP tools are **added per session** from ACP `mcp_servers`, never hardcoded.
- ACP delivers `mcp_servers` as typed objects: `HttpMcpServer(name, url, headers: list[HttpHeader{name,value}], type)` and `McpServerStdio(name, command, args, env, …)`. Translate the header list → dict for `lovelaice.mcp.connect()`.
- System prompt is assembled from the tool registry **at agent construction** (`Agent.__init__` → `assemble_system_prompt(tools=registry)`). MCP tools must therefore be passed to the factory, not appended after build.
- Per-session MCP background sessions must be torn down on session close (aegis opens/closes sessions constantly) — no "OS reaps it" leak.
- Ship as lovelaice **2.8.0** (additive). aegis bumps its floor to `>=2.8,<3` and relocks from PyPI after publish.
- Tests inline; `LOVELAICE_FAKE_LLM=1` hermetic; real-model probe re-run at the end.
- TDD: failing test first, minimal impl, commit per unit.

## File Structure

**lovelaice:**
- Modify `src/lovelaice/mcp.py` — add `ManagedMcpSession` (HTTP+stdio, background loop, `aclose()`), `start_managed_session(spec)`, and `build_agent_tools(specs) -> (tools, sessions)`.
- Modify `src/lovelaice/coding/host.py` — `create_coding_agent(..., extra_tools=None)`.
- Modify `src/lovelaice/acp/v1/server.py` — `new_session` connects mcp_servers, passes tools to factory, tracks sessions; teardown on `cancel`/close; helper `_mcp_specs_from_acp(mcp_servers)`.
- Modify `src/lovelaice/acp/v1/__main__.py` — `_default_factory(*, extra_tools=None)`.
- Create `tests/mcp/test_managed_session.py`, extend `tests/acp/v1/test_server_v1.py`.

**aegis:**
- No code change beyond dependency bump; the driver already passes a real `mcp_url`. Add `tests/test_lovelaice_mcp_live.py` — native agent calls `aegis_claim` end-to-end.

## Task 1: `lovelaice.mcp` — managed session (HTTP + stdio + teardown)

**Files:** Modify `src/lovelaice/mcp.py`; Test `tests/mcp/test_managed_session.py`.

**Interfaces:**
- Produces: `class ManagedMcpSession` with async `call_tool(name, kwargs)` and `aclose()`; `start_managed_session(spec: dict) -> ManagedMcpSession` (dispatches `{url}`→HTTP, `{command}`→stdio on a dedicated background loop/thread, initializes, lists tools available as `.tools`). Generalizes the existing stdio-only `_start_session_in_background` to both transports with a stoppable park loop (reference: warden `_acp_driver._start_http_mcp`).

- [ ] **Step 1: Write the failing test** (stdio echo server is the hermetic case)

```python
# tests/mcp/test_managed_session.py
import sys
import pytest
from lovelaice.mcp import start_managed_session

SERVER = '''
import asyncio
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("echo")
@mcp.tool()
def ping(msg: str) -> str:
    return f"pong:{msg}"
mcp.run(transport="stdio")
'''

@pytest.mark.asyncio
async def test_managed_stdio_session_lists_and_calls_and_closes(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(SERVER)
    sess = start_managed_session({"command": sys.executable, "args": [str(script)]})
    try:
        assert any(t.name == "ping" for t in sess.tools)
        result = await sess.call_tool("ping", {"msg": "hi"})
        text = "".join(getattr(p, "text", "") for p in (getattr(result, "content", None) or []))
        assert "pong:hi" in text
    finally:
        await sess.aclose()
```

- [ ] **Step 2: Run to verify it fails** — `ImportError: cannot import name 'start_managed_session'`.
  Run: `cd repos/lovelaice && uv run python -m pytest tests/mcp/test_managed_session.py -v`

- [ ] **Step 3: Implement** — add to `src/lovelaice/mcp.py`:

```python
class ManagedMcpSession:
    """An MCP ClientSession kept alive on a dedicated background loop/thread,
    with explicit teardown. Supports HTTP ({url}) and stdio ({command})."""

    def __init__(self, loop, session, thread, stop_event, tools):
        self._loop = loop
        self._session = session
        self._thread = thread
        self._stop = stop_event
        self.tools = tools

    async def call_tool(self, name: str, kwargs: dict):
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, kwargs), self._loop)
        return await asyncio.wrap_future(fut)

    async def aclose(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5.0)


def start_managed_session(spec: dict) -> "ManagedMcpSession":
    if ClientSession is None:
        raise RuntimeError("mcp Python SDK not installed")
    ready = threading.Event()
    holder: dict[str, Any] = {}

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()

        async def _go():
            if "url" in spec:
                cm = _http_session(spec)
            elif "command" in spec:
                cm = _stdio_session(spec)
            else:
                raise ValueError(f"unrecognized MCP config: {spec!r}")
            async with cm as session:
                tools = (await session.list_tools()).tools
                holder.update(loop=loop, session=session, stop=stop, tools=list(tools))
                ready.set()
                await stop.wait()

        try:
            loop.run_until_complete(_go())
        except BaseException as e:  # noqa: BLE001
            holder["error"] = e
            ready.set()
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True, name=f"mcp-{spec.get('name','?')}")
    t.start()
    ready.wait(timeout=20.0)
    if "error" in holder:
        raise holder["error"]
    if "session" not in holder:
        raise RuntimeError(f"MCP server {spec.get('name')!r} did not init within 20s")
    return ManagedMcpSession(holder["loop"], holder["session"], t,
                             holder["stop"], holder["tools"])
```

Note: `_http_session`/`_stdio_session` are the existing context managers in `mcp.py`; reuse them verbatim.

- [ ] **Step 4: Run to verify pass.** Run the test from Step 2. Expected: PASS.
- [ ] **Step 5: Commit** — `git add src/lovelaice/mcp.py tests/mcp/test_managed_session.py && git commit -m "feat(mcp): ManagedMcpSession — HTTP+stdio background session with teardown"`

## Task 2: wrap managed-session tools as `AgentTool`s

**Files:** Modify `src/lovelaice/mcp.py`; Test `tests/mcp/test_managed_session.py`.

**Interfaces:**
- Produces: `build_agent_tools(specs: list[dict]) -> tuple[list[AgentTool], list[ManagedMcpSession]]` — starts one managed session per spec, wraps each tool via `_MCPTool` (existing) into an `AgentTool(inner=..., kind="other")`, returns tools + sessions (sessions retained for teardown). A spec that fails to start logs + is skipped (no tools, no session).

- [ ] **Step 1: Failing test** — assert `build_agent_tools([echo_spec])` returns one `AgentTool` named `mcp:echo:ping` and one session; `aclose` the session after.
- [ ] **Step 2: Run → fail** (`build_agent_tools` undefined).
- [ ] **Step 3: Implement** — wrap `session.tools` with the existing `_wrap_mcp_tool`, box each in `AgentTool(inner=<_MCPTool>, kind="other")`; return `(tools, sessions)`. Skip a spec on exception with a `print("[mcp] …skip…")`.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(mcp): build_agent_tools wraps managed MCP tools as AgentTools`

## Task 3: `create_coding_agent(extra_tools=…)`

**Files:** Modify `src/lovelaice/coding/host.py`; Test `tests/test_coding_host.py` (extend or create).

**Interfaces:**
- Consumes: existing `create_coding_agent(*, model, session_path, cwd, base_url=None, api_key=None)`.
- Produces: adds `extra_tools: list[AgentTool] | None = None` appended to the built-in `tools` list before `Agent(...)` construction (so they're in the registry → system prompt).

- [ ] **Step 1: Failing test** — build with `extra_tools=[<a trivial AgentTool>]`; assert `agent.harness.tools.get(<name>)` is not None.
- [ ] **Step 2: Run → fail** (`extra_tools` unexpected kwarg).
- [ ] **Step 3: Implement** — `tools = [read, bash, *(extra_tools or [])]`.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(coding): create_coding_agent accepts extra_tools`

## Task 4: v1 `new_session` connects mcp_servers + factory passes tools; teardown

**Files:** Modify `src/lovelaice/acp/v1/server.py`, `src/lovelaice/acp/v1/__main__.py`; Test `tests/acp/v1/test_server_v1.py`.

**Interfaces:**
- Consumes: `start_managed_session`/`build_agent_tools`; typed `HttpMcpServer`/`McpServerStdio`.
- Produces:
  - `_mcp_specs_from_acp(mcp_servers) -> list[dict]` — static; `HttpMcpServer` → `{"name", "url", "headers": {h.name: h.value for h in headers}}`; `McpServerStdio` → `{"name", "command", "args", "env"}`.
  - `new_session` builds `(tools, sessions)` from specs, calls `self._agent_factory(mcp_tools=tools)`, stores `self._mcp_sessions[sid] = sessions`.
  - factory contract widens to accept `mcp_tools` (default `_default_factory(*, mcp_tools=None)` → `create_coding_agent(..., extra_tools=mcp_tools)`).
  - teardown: `close_session`/`cancel` (and a new `_teardown(sid)`) call `await s.aclose()` for each session.

- [ ] **Step 1: Failing test** — `_mcp_specs_from_acp` maps a fake `HttpMcpServer`-shaped object (name/url/headers list of name/value) to `{"name","url","headers":{...}}`; and a stdio one to `{"name","command","args"}`.

```python
def test_mcp_specs_from_acp_maps_http_and_stdio():
    class H:  # HttpMcpServer-shaped
        name, url = "aegis", "http://x/mcp"
        headers = [type("Hdr", (), {"name": "Authorization", "value": "Bearer z"})()]
    class S:  # McpServerStdio-shaped
        name, command, args, env = "local", "mytool", ["--x"], None
    specs = AcpServerV1._mcp_specs_from_acp([H(), S()])
    assert specs[0] == {"name": "aegis", "url": "http://x/mcp",
                        "headers": {"Authorization": "Bearer z"}}
    assert specs[1] == {"name": "local", "command": "mytool", "args": ["--x"], "env": None}
```

- [ ] **Step 2: Run → fail** (`_mcp_specs_from_acp` undefined).
- [ ] **Step 3: Implement** the static mapper (duck-typed on attributes: `hasattr(s, "url")` → HTTP else stdio), the `new_session` wiring (`build_agent_tools` → `agent_factory(mcp_tools=tools)` → store sessions), `_default_factory(*, mcp_tools=None)`, and `_teardown(sid)` invoked from `cancel`/`close_session`.
- [ ] **Step 4: Run → pass** the mapper test + the existing v1 suite (factory now takes `mcp_tools`; ensure `new_session` with no mcp_servers still works — `build_agent_tools([])` → `([], [])`).
- [ ] **Step 5: Commit** — `feat(acp-v1): per-session MCP attach from ACP mcp_servers + teardown`

## Task 5: version bump + publish 2.8.0

**Files:** `pyproject.toml` (2.8.0), `CHANGELOG.md`.

- [ ] **Step 1:** Bump `version = "2.8.0"`; add CHANGELOG § 2.8.0 (per-session MCP attach; HTTP+stdio managed sessions; `create_coding_agent(extra_tools=…)`).
- [ ] **Step 2:** Full suite green — `uv run python -m pytest -q` (incl. legacy ACP suite).
- [ ] **Step 3:** Commit, push, `gh release create v2.8.0 --target main --title "v2.8.0 — per-session MCP attach" --notes "…"` (triggers OIDC publish via `release.yaml`).
- [ ] **Step 4:** Verify `curl -s https://pypi.org/pypi/lovelaice/json | jq -r .info.version` == `2.8.0`.

## Task 6: aegis bump + MCP-plane live proof

**Files:** aegis `pyproject.toml` (`lovelaice>=2.8,<3`); `tests/test_lovelaice_mcp_live.py`.

**Interfaces:**
- Consumes: aegis `AegisMCP` runtime (real `mcp_url`), `LovelaiceDriver`.
- Produces: a live test (skips off PATH / without key) where a native lovelaice agent, given the aegis MCP server via the driver's normal `mcp_url`, calls `aegis_claim` and the claim is observed in the registry.

- [ ] **Step 1:** Relock aegis from PyPI (`uv lock && uv sync`); confirm `lovelaice==2.8.0`.
- [ ] **Step 2: Failing/real test** — stand up the aegis MCP runtime bound to a `SessionManager`, spawn a `LovelaiceDriver` session with that `mcp_url`, prompt the agent to `aegis_claim` a path, assert a `ToolUse` for the claim + the claim present via `aegis_claims`. Real model (haiku) — gate on key + `lovelaice-acp`.
- [ ] **Step 3: Run → confirm** the native agent reached the aegis plane.
- [ ] **Step 4:** Re-run `.playground/lovelaice-probe/probe.py` (regression) and append MCP findings to `FINDINGS.md`.
- [ ] **Step 5: Commit** — aegis `build: lovelaice>=2.8; native agent reaches the aegis MCP plane`.

## Self-Review

**Spec coverage (Part 1b):** ✅ read ACP `mcp_servers` (T4), HTTP+stdio managed lifecycle upstreamed into `lovelaice.mcp` (T1–T2), header-list→dict translation (T4), attach to session agent at construction (T3–T4), teardown on close (T4), native agent calls `aegis_*` proven (T6). Warden untouched (legacy server frozen; warden keeps its own factory).

**Placeholder scan:** none — every step has concrete code or an exact command.

**Type consistency:** `start_managed_session(spec)->ManagedMcpSession` (T1) → `build_agent_tools`→`(tools, sessions)` (T2) → `create_coding_agent(extra_tools=)` (T3) → `_default_factory(mcp_tools=)`/`new_session` store+teardown (T4). `_mcp_specs_from_acp` output shape matches `lovelaice.mcp.connect()` input (`{url|command, headers dict}`).
