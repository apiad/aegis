# Lovelaice native ACP-v1 agent — probe findings (2026-07-10)

Probe: `probe.py` drives a native lovelaice agent through `LovelaiceDriver`
against a **real** model (OpenRouter `anthropic/claude-haiku-4-5`), asking it
to read a file. Complements the FAKE_LLM tests.

## Result: PASS (after one real bug fixed)

Final run — clean full-path round-trip:

```
EVENT KINDS: SystemInit, AssistantText, ToolUse, ToolResult, AssistantText, Result
TOOL CALLS:  [('read', 'secret.txt')]
ANSWER:      ... The magic number is **4217**.
READ CALLED: True   ANSWER HAS 4217: True
```

The native agent (no external harness — lingo → OpenRouter directly) called
the `read` tool and answered correctly. tool_use↔tool_result pairing and the
message/tool render events all flow through aegis's existing ACP event surface.

## Bug the probe caught (fixed)

`AcpServerV1.prompt` extracted prompt text with `b.get("text")` — dict-only.
Over the real ACP wire the SDK delivers **typed `TextContentBlock` objects**,
so extraction returned `""` and the agent saw an **empty prompt** (it replied
with a generic "how can I help"). FAKE_LLM masked it (canned response ignores
input); the FAKE stdio test used the SDK but never asserts the prompt reached
the model. Fix: `_prompt_text()` handles dicts **and** content-block objects,
plus a regression unit test. lovelaice `c12c8ad`.

**Lesson for VS2–VS4 plans:** FAKE_LLM proves protocol wiring, not that input
reaches the model. Keep a real-model probe in the loop for each slice.

## Remaining (expected, deferred by design)

- Metrics show 0/0 tokens — `usage` surfacing is VS4.
- No per-session MCP tools yet (native agent can't call `aegis_*`) — VS2.
- Only read/bash tools — write/edit/glob/list is VS3.

## VS2 — per-session MCP attach (2026-07-12)

Proof: a native lovelaice agent, given an aegis-plane MCP server via the
driver's `mcp_url`, calls a tool on it. `test_lovelaice_mcp_live.py` +
`inproc_repro.py`. Final: `tools on agent: ['read','bash','mcp_aegis_aegis_claim']`,
model called it, server `received: ['src/foo.py']`, `stop_reason: end_turn`.
The native agent now reaches the aegis MCP plane — more capable than the
gemini/opencode workers (which can't do per-session MCP injection).

### Two real bugs the probe caught (both masked by hermetic tests)

1. **Colon in MCP tool name.** `mcp:<server>:<tool>` violates the LLM
   tool-name pattern `^[a-zA-Z0-9_-]{1,128}$` — Anthropic/OpenAI 400 the whole
   request. Sanitized to `mcp_<server>_<tool>`; the original name is still used
   for the MCP call. (warden avoided this by using the bare tool name.)
2. **Blocking connect in the async loop.** `new_session` called the blocking
   `build_agent_tools` (which waits on per-server connect threads) directly in
   the event loop, freezing the ACP server → surfaced as a generic
   `RequestError: Internal error`. Fixed with `asyncio.to_thread`.

**Debugging note:** the ACP SDK swallows agent-side exceptions as "Internal
error". To see the real traceback, reproduce **in-process** (`inproc_repro.py`)
rather than through the subprocess. And run the MCP server in a **separate
thread** from the probe — sharing one event loop deadlocks the blocking
`ready.wait()` against the server it's trying to reach.

## Still to eyeball (human)

The interactive TUI render (`aegis` full-screen, open a `lovelaice` tab, watch
the read tool-call block + answer stream). Scripted driver path is confirmed;
the TUI uses the same event surface, so this is a visual confirmation.
```yaml
# /tmp/lovel-probe/.aegis.yaml  (outside Workspace)
agents:
  local: { provider: lovelaice, model: anthropic/claude-haiku-4-5,
           api_key_file: /home/apiad/Workspace/.claude/openrouter.token }
default_agent: local
```
