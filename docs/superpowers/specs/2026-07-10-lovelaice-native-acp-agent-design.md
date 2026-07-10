# Native lovelaice agent for aegis, over official ACP v1

**Date:** 2026-07-10
**Status:** design — approved, pre-plan
**Spans:** `repos/aegis` (driver + config + dependency) and `repos/lovelaice`
(new ACP-v1 server + MCP wiring + native toolset). Companion note:
`repos/lovelaice/docs/2026-07-10-acp-v1-and-native-toolset.md`.

## Motivation

aegis is a meta-harness: today every agent it runs is an external harness CLI
(Claude Code, Gemini, OpenCode). We want a **native agent that depends on no
external harness** — one that talks to a model API or a local endpoint directly
— so aegis can run local models (Ollama-class) and direct-API models without a
third-party CLI in the loop.

lovelaice is exactly that runtime: a native tool-calling ReAct loop over `lingo`
→ any OpenAI-compatible endpoint, already exposing an ACP surface. The end state:
**aegis ships lovelaice as a dependency and offers it as the default,
zero-harness agent.** `pip install aegis` puts `lovelaice-acp` on PATH; point
`base_url` at a local endpoint or set an API key, and you have an agent with no
external harness.

The blocker is a protocol-dialect gap plus incomplete MCP wiring in lovelaice,
detailed below. This spec closes both **without breaking lovelaice's existing
client (warden/ainbox)**.

## Goals

- lovelaice speaks **official ACP v1** (via the `agent-client-protocol` SDK), so
  aegis's existing generic `AcpDriver` drives it with a thin shim.
- The native agent is **first-class in the aegis MCP plane** — it can call
  `aegis_enqueue` / `aegis_handoff` / `aegis_spawn` / `aegis_claim`, via
  per-session MCP injection through ACP `new_session(mcp_servers=…)`.
- A **strong native toolset** on par with the gemini/opencode basics:
  `read`, `write`, `edit`, `glob`, `list`, `bash`. No subagents, no skills.
- **Session resume** (`load_session`) works — it is both an aegis feature and
  part of the warden compat surface.
- **Existing clients keep working untouched** with a documented, opt-in
  upgrade path.

## Non-goals

- No subagents, no skill system, no plan/todo tooling in the native agent (v1).
- No forced migration of warden/ainbox — their change is opt-in and out of this
  project's scope (documented as a checklist only).
- No rewrite of lovelaice's legacy `AcpServer` — it is frozen, not replaced.
- No billing/proxy layer — native subscription/API pass-through, as elsewhere in
  aegis.

## Current state (ground truth)

### aegis side — ready
- `src/aegis/drivers/acp.py`: generic `AcpSession` + `AcpDriver` on the official
  `agent-client-protocol` SDK (pinned `>=0.10`; 0.10.0 installed). `GeminiDriver`
  / `OpenCodeDriver` are ~5-line `BASE_CMD` shims.
- `AcpSession.start()` sends `protocol_version=1`, calls
  `new_session(cwd, mcp_servers=[{type:"http", name:"aegis", url, headers:[]}])`
  or `load_session(session_id=…)`; drains `session_update` notifications as
  typed SDK objects via `_AegisAcpClient.session_update(session_id, update, …)`.
- `AcpDriver.supports_resume = True`; `resume()` calls `load_session`.
- Driver registry: `DRIVERS` dict in `drivers/__init__.py`, keyed by harness
  string. Provider config classes in `config/__init__.py`: `_ProviderBase`
  subclasses `ClaudeCode` / `GeminiCLI` / `OpenCode`, registered in
  `_PROVIDERS_BY_NAME`. Adding a provider = new class + two registry entries +
  a shim driver.
- Env into the subprocess currently flows **only** through the pre-spawn-hook
  path (`AcpSession._apply_pre_spawn_hooks`). There is no direct `extra_env`
  seam on the driver yet.

### lovelaice side — needs work
- `lovelaice-acp` console script → `lovelaice.acp.__main__:main` builds an
  `AcpServer` with `tools=[]`. Config from env: `LOVELAICE_MODEL`,
  `LOVELAICE_BASE_URL`, `OPENROUTER_API_KEY`/`OPENAI_API_KEY`.
- `lovelaice.acp.server.AcpServer`: **hand-rolled JSON-RPC**, NOT the official
  SDK. Advertises `protocolVersion "0.1"`; emits **flat** `session/update`
  params (`{sessionId, sessionUpdate, content, toolCallId}` — not the SDK's
  nested `{sessionId, update:{…}}`); `loadSession: False`; no `usage` in
  `PromptResponse`. Carries **non-ACP extensions**: `session/new` +
  `conversationId` → `messages` replay; `workflow/run`; `conversation/archive`;
  and a `conversation_store` + `agent_factory(conversation=None)` constructor.
- `lovelaice.coding.host.create_coding_agent` wires only `read` + `bash`, with
  `path_guard` + `bash_prefix_guard` (`coding/hooks.py`).
- **Reusable tool library already exists:** `lovelaice.tools.files`
  (`read`, `write`, `edit`, `list_`) and `lovelaice.tools.search`
  (`glob`, `grep`) — all `@tool`-decorated `lingo.Tool`s.
- `lovelaice.mcp`: `connect()` handles HTTP **and** stdio; `_MCPTool` wraps an
  MCP tool → `lingo.Tool` with verbatim `json_schema`; `register_mcp_tools`
  attaches tools to an agent. **But:** nothing reads ACP `mcp_servers`;
  `register_mcp_tools`'s live/background path is **stdio-only** (HTTP session
  exists only as a context manager); header shape expected is a **dict** while
  ACP sends a **list** of `{name,value}`; no per-session teardown. Its own
  AGENTS.md flags MCP as "currently unwired… future task."

### The only direct ACP client of lovelaice is warden
- `repos/warden/warden/agent/runtime.py` hand-rolls a JSON-RPC client speaking
  the "0.1" flat dialect: `initialize {protocolVersion:"0.1"}`, `session/new`
  (+ optional `conversationId`) → `{sessionId, conversationId, messages}`,
  `session/prompt`, `session/cancel`, `workflow/run` (blocking + streaming),
  `conversation/archive`. `warden.agent.events.translate_acp_notification`
  reads the **flat** shape (`params.get("sessionUpdate")`, `params.get("toolCallId")`).
- `repos/warden/warden/agent/_acp_driver.py` spawns lovelaice's `AcpServer`
  **class** (not the `lovelaice-acp` script), attaching MCP tools via env
  (`WARDEN_AGENT_MCP_SERVERS`) and a **reimplemented** HTTP-MCP-on-a-thread
  (`_start_http_mcp` + a duplicate `_MCPLingoTool`) — because lovelaice's own
  `register_mcp_tools` can't do HTTP with a managed lifecycle.
- warden pins `lovelaice>=2.6.0,<3.0`. **ainbox** is a warden HTTP/WS client;
  it never touches lovelaice ACP directly.

## Architecture

Four parts. The protocol is the enabling spine; MCP + toolset make the native
agent first-class; the aegis wiring lands it as a shipped default.

### Part 1 — new `lovelaice.acp.v1` server on the official SDK

A new module (clean-room; legacy untouched), implementing the `acp.Agent`
interface and served via `acp.run_agent` over stdio — symmetric with aegis's
`acp.Client` / `connect_to_agent`.

- **Methods:** `initialize` (advertise real `acp.PROTOCOL_VERSION` +
  promptCapabilities), `new_session` (accept `cwd` + `mcp_servers`), `prompt`,
  `cancel`, **`load_session`** (in scope).
- **Event translation** via SDK builders (`update_agent_message_text`,
  `start_read_tool_call` / `start_edit_tool_call` / `start_tool_call`,
  `update_tool_call` + `tool_diff_content`) — nested wire shape and camelCase are
  correct-by-construction; the flat-dict bug class disappears.
- **`PromptResponse`** returns `stopReason` **and** token `usage` from lingo, so
  aegis's status-line metrics stop reading 0/0.
- **Extensions preserved** so warden has a v1 home later:
  - `workflow/run` → ACP `ext_method` (the SDK exposes `ext_method` /
    `ext_notification`; aegis's client already stubs them).
  - `conversation/archive` → ACP `ext_notification`.
  - conversation replay/resume → mapped onto ACP-native **`load_session`**
    (ACP `sessionId` ⟷ lovelaice `conversationId`).
- **Constructor mirrors legacy:** `AcpServerV1(agent_factory=…,
  conversation_store=…)` with the same `agent_factory(conversation=None)`
  contract, so a host that wires its own tools (warden) migrates by changing an
  import + the wire dialect, not its tool-wiring.

### Part 1b — first-class per-session MCP attach

The v1 `new_session` / `load_session` reads ACP `mcp_servers`, and for each:
translates the ACP header-list (`[{name,value}]`) → lovelaice's `connect()`
shape, connects (HTTP **or** stdio) on a **managed background session**, wraps
its tools via the existing `_wrap_mcp_tool`, attaches them to **that session's**
agent, and tears them down on session close / `cancel`.

- The HTTP-on-a-background-thread lifecycle is **upstreamed into
  `lovelaice.mcp`** (warden's `_start_http_mcp` is the reference impl), so
  `register_mcp_tools` gains a first-class HTTP path with teardown.
- Result: an aegis-driven lovelaice agent can call the aegis MCP plane
  (`aegis_enqueue`, `aegis_handoff`, `aegis_spawn`, `aegis_claim`). This is
  strictly **more** capable than gemini/opencode workers, whose MCP config is
  global (no per-session injection).

### Part 2 — full native toolset in the default entrypoint

Wire the full basic set into the **default `lovelaice-acp` agent factory**
(`create_coding_agent`), reusing the existing library tools:

| tool  | source                         | ACP `kind` | guard                         |
|-------|--------------------------------|------------|-------------------------------|
| read  | `coding/tools/read` (truncation)| read      | —                             |
| bash  | `coding/tools/bash`            | execute    | `bash_prefix_guard` (existing)|
| write | `tools/files.write`            | edit       | `path_guard` (cwd)            |
| edit  | `tools/files.edit`             | edit       | `path_guard`                  |
| list  | `tools/files.list_`            | search     | `path_guard`                  |
| glob  | `tools/search.glob`            | search     | —                             |

- Thin coding-host wrappers where the lib tool is too naive (write parent-dir
  creation, edit unambiguous-match error, list/glob output caps — matching the
  care in `coding/tools/read.py`).
- Update `CODING_PREAMBLE` to describe the fuller toolset.
- **Scope guard:** the toolset is an **entrypoint default**, never forced on
  hosts — warden keeps its own MCP-only factory. No subagents, no skills, no
  grep-as-tool beyond `glob`.

### Part 3 — aegis: driver + provider + dependency

- `src/aegis/drivers/lovelaice.py`: `LovelaiceDriver(AcpDriver)`,
  `BASE_CMD = ["lovelaice-acp"]`. Adds a small **`extra_env` seam** on the
  driver → `AcpSession` (cleaner than a bespoke pre-spawn hook) to inject
  `LOVELAICE_MODEL` / `LOVELAICE_BASE_URL` / API key at spawn.
- `src/aegis/config/__init__.py`: new `Lovelaice(_ProviderBase)` —
  `name: Literal["lovelaice"]`, fields `model`, `base_url: str | None` (point at
  a local endpoint for Ollama; omit for OpenRouter), `api_key_file: str | None`
  (read at spawn → env; **never inline the key**). Register in
  `_PROVIDERS_BY_NAME` and `DRIVERS`.
- aegis `pyproject.toml`: add `lovelaice` as a dependency (no circular dep —
  lovelaice does not depend on aegis) so `lovelaice-acp` is on PATH by default.
- Follow-on (noted, not core): offer `lovelaice` as a zero-harness default in
  the ConfigPanel onboarding.

## Compatibility & the warden/ainbox upgrade path

**Now: nothing changes.** The legacy `lovelaice.acp.server.AcpServer` class stays
byte-compatible within the 2.x line; the new work ships as lovelaice **2.7.0**
(minor, additive). warden's `>=2.6.0,<3.0` pin is satisfied; ainbox is untouched
(HTTP client of warden). The `lovelaice-acp` **script** is repointed to the v1
server (what Zed and aegis expect) — safe because warden spawns the *class*, not
the script. Legacy stays reachable via class import (optional
`lovelaice-acp-legacy` script if a CLI is wanted).

**Later, opt-in (out of this project's scope — documented as a checklist in
`repos/lovelaice/know-how/`):** warden migrates in one bounded delta —
1. `runtime.py`: replace the hand-rolled JSON-RPC client with the official
   `acp` SDK client → send `protocolVersion:1`, read notifications from nested
   `params.update`, use `load_session` instead of `session/new`+`conversationId`,
   call `workflow/run` via ext-method.
2. `events.py`: update `translate_acp_notification` to the nested shape.
3. `_acp_driver.py`: point at `AcpServerV1` (keep `_make_factory` tool-wiring),
   and — once Part 1b lands — drop the bespoke `_start_http_mcp` /
   `_MCPLingoTool` in favor of passing `mcp_servers` through ACP `new_session`.

## Vertical slices

1. **VS1 (spine):** minimal v1 server (initialize / new_session / prompt /
   cancel + agent-message + a single `read` tool) + aegis `LovelaiceDriver` +
   `Lovelaice` provider + aegis→lovelaice dependency → **an aegis tab where a
   native lovelaice agent reads a file and answers.** Thinnest end-to-end path.
2. **VS2 (MCP plane):** Part 1b — per-session HTTP+stdio MCP attach; prove the
   native agent calling an aegis MCP tool (e.g. `aegis_claim`) live.
3. **VS3 (toolset):** write / edit / glob / list wired with guards + render kinds.
4. **VS4 (parity/polish):** `load_session` resume, token `usage` surfacing,
   streaming message chunks, `workflow/run` + `conversation/archive` ext-methods.

Each slice is an honest stop point.

## Testing

Tests stay inline (not delegated). Per repo:

- **lovelaice:** SDK-client round-trip tests against the v1 server
  (initialize / new_session / prompt / cancel / load_session); per-session MCP
  attach test (HTTP + stdio) with teardown; tool-wiring tests for the new
  coding tools. Legacy `AcpServer` tests remain untouched and green (the compat
  guarantee). Manual smoke: `lovelaice-acp` handshakes with an official ACP
  client; `LOVELAICE_FAKE_LLM=1` for hermetic runs.
- **aegis:** driver-registry + `Lovelaice` provider-config tests; an
  `extra_env` injection test; a live round-trip mirroring
  `tests/test_drivers_multiprovider_live.py` that **auto-skips when
  `lovelaice-acp` is off PATH**.
- **Probe first:** before VS1 lands, spawn the v1 server and point an aegis
  `AcpSession` at it (FAKE_LLM, then one live run) to confirm the handshake and
  a tool round-trip render — mirroring `.playground/acp-probe/FINDINGS.md`.

## Decisions made

- **Separate v1 module**, not dual-dialect negotiation in one server — keeps the
  frozen legacy path risk-free.
- **`load_session` in v1 scope** — it is part of the compat surface and aegis's
  resume feature.
- **API key via `api_key_file`**, read to env at spawn — never inline the secret.
- **lovelaice-specific surface (`workflow/run`, archive) preserved as ACP
  extensions** rather than dropped, giving warden a real v1 destination.

## Open questions

- Should aegis pin lovelaice to a floor (`>=2.7.0`) or vendor a known-good
  range? (Lean: floor pin `>=2.7,<3`.)
- Do we ship an optional `lovelaice-acp-legacy` script, or leave legacy
  class-import-only? (Lean: class-import-only until something needs the CLI.)
