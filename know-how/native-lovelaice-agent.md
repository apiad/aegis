# know-how: the native lovelaice agent

**Reach for this when** you work on `drivers/lovelaice.py`, the `Lovelaice`
provider, or debug a native (harness-free) agent tab — the one that runs local
or direct-API models with no external CLI.

## What it is

aegis depends on `lovelaice` (from PyPI) and drives `lovelaice-acp` — lovelaice's
ACP v1 stdio server — over the generic `AcpDriver`. So a fresh `pip install aegis`
has a native agent out of the box: point `base_url` at a local endpoint (Ollama)
for local models, or set a key for OpenRouter/direct API. No Claude/Gemini/OpenCode
CLI in the loop; lovelaice calls the model directly via lingo.

`LovelaiceDriver(AcpDriver)` (`BASE_CMD = ["lovelaice-acp"]`) overrides
`extra_env(agent)` to inject `LOVELAICE_MODEL` / `LOVELAICE_BASE_URL` /
`OPENROUTER_API_KEY` (read from the provider's `api_key_file`) at spawn.

## Config

`.aegis.yaml`:

```yaml
agents:
  local:
    provider: lovelaice
    model: anthropic/claude-haiku-4-5      # or a local model id
    base_url: https://openrouter.ai/api/v1 # or http://localhost:11434/v1 for Ollama
    api_key_file: /home/apiad/Workspace/.claude/openrouter.token
default_agent: local
```

`Lovelaice(_ProviderBase)` in `config/__init__.py`: `model`, `base_url`,
`api_key_file`, `permission` (default `full`). Never inline the key — always a
scoped `api_key_file` path read at spawn.

## What works (and where it lives)

- **Per-session MCP** — aegis's `AcpSession.start()` passes its MCP server via
  `new_session(mcp_servers=[…])`, so the native agent can call the aegis plane
  (`aegis_claim`, `aegis_enqueue`, …). This is *more* than Gemini/OpenCode can do
  (their MCP is global, not per-session).
- **Streaming** — lovelaice streams `AgentMessageChunk`s; they surface as
  incremental `AssistantText` events (many per turn).
- **Resume** — `LovelaiceDriver.resume(agent, cwd, mcp, handle, session_id)`
  calls ACP `load_session`; lovelaice restores context from its per-session jsonl.
  Requires `AcpSession.session_id` (the property that exposes the latched id — a
  caller passes it back to `resume()`).
- **Cancel** — `AcpSession.interrupt()` sends ACP `session/cancel`; a running
  turn stops mid-stream and the terminal `Result` flows out normally. (Escape in
  the TUI routes here.)
- **Token usage** — real metrics in the status line (lovelaice returns `usage`
  in the ACP `PromptResponse`).

The `extra_env` seam and the `session_id` / `interrupt` overrides live in the
**generic** `drivers/acp.py` (they benefit every ACP driver), not in
`lovelaice.py`.

## Testing — real model, not just FAKE

Hermetic tests (`tests/test_lovelaice_driver.py`) cover config/argv/env/session_id/
interrupt. The **live** tests (`test_lovelaice_live.py`, `test_lovelaice_mcp_live.py`,
`test_lovelaice_resume_live.py`) auto-skip when `lovelaice-acp` is off PATH or no
key file exists — run them with a real `anthropic/claude-haiku-4-5`.

**Lesson (burned 3×):** FAKE_LLM proves protocol wiring but not that input reaches
the model or that tool schemas are API-valid. Real-model probes caught: empty
prompt from typed ContentBlocks, colon-in-MCP-tool-name (LLM 400), a blocking MCP
connect freezing the ACP loop, and resume silently starting fresh because
`session_id` wasn't exposed. Keep a real-model probe per change. To see the real
traceback (the ACP SDK hides agent errors as "Internal error"), reproduce
in-process on the lovelaice side.

## Releasing across the two repos

lovelaice ships to PyPI via its own OIDC `release.yaml` (a `gh release create`
triggers it — no token). After it lands, bump aegis's floor
(`lovelaice>=X,<3`), `uv lock --refresh --upgrade-package lovelaice` (the
`/simple/` index lags a fresh publish by minutes — check
`curl -s https://pypi.org/simple/lovelaice/`), `uv sync`, re-run the live tests.

Design + full slice history (VS1–VS5):
`docs/superpowers/specs/2026-07-10-lovelaice-native-acp-agent-design.md` and
`docs/superpowers/plans/2026-07-*`.
