# Drivers

A **driver** is the layer that owns one coding-agent CLI subprocess.
It speaks the CLI's structured protocol, sends user messages, and
yields typed events (`AssistantText`, `ToolUse`, `ToolResult`,
`Result`, etc.) to the surrounding session. Above the driver, aegis
treats every provider identically.

Four drivers ship today: `claude-code`, `gemini`, `opencode`, and
`copilot`. All give the same UX surface, multi-turn, streaming,
cancellation, and per-session MCP injection.

## How drivers talk to each CLI

| Provider | Protocol | Mode | Per-session MCP | OAuth |
|---|---|---|---|---|
| Claude Code | stream-json (bidirectional) | `claude -p` with `--input-format/--output-format stream-json` | `--mcp-config` per invocation | Native |
| Gemini CLI  | [ACP](https://github.com/zed-industries/agent-client-protocol) | `gemini --acp` | `session/new(mcpServers=[…])` | Pass-through |
| OpenCode    | ACP | `opencode acp` | `session/new(mcpServers=[…])` | Pass-through |
| Copilot CLI | ACP | `copilot --acp` | `session/new(mcpServers=[…])` | Pass-through |

ACP (Agent Client Protocol) is Zed's JSON-RPC-over-stdio specification
for editor↔agent communication. Aegis uses the official Python SDK
[`agent-client-protocol`](https://pypi.org/project/agent-client-protocol/)
to drive Gemini, OpenCode, and Copilot through it.

## What "feature parity" means

Whatever you can do with one provider, you can do with any. Concretely:

- **Multi-turn**: send N user messages to one session; the agent keeps
  context. Implemented via `session/prompt` on ACP and via stdin
  user-message frames on stream-json.
- **Streaming**: tokens, thinking blocks, and tool calls arrive
  incrementally and render live.
- **Cancellation**: `Escape` in the TUI cancels the active turn; the
  driver sends the protocol's cancel and the agent stops at the next
  safe point.
- **Per-session MCP injection**: every spawned agent gets a unique MCP
  server URL bound to its session, so calls from agent ↔ aegis are
  tagged with the correct sender automatically. No global MCP config
  pollution.

## Picking models

Each provider's `model` string is whatever its native CLI accepts:

| Provider | Examples |
|---|---|
| `ClaudeCode` | `opus`, `sonnet`, `haiku` |
| `GeminiCLI`  | `gemini-3-flash-preview`, `gemini-3.1-pro-preview` |
| `OpenCode`   | `opencode/kimi-k2.6`, `opencode/glm-5.1`, `opencode/minimax-m2.7`, `opencode/qwen3.6-plus` |
| `CopilotCLI` | `claude-sonnet-4.5`, `gpt-5.4`, `auto` |

For OpenCode, run `opencode models` to see what's installed on your
machine. For Gemini, see Google's model docs. For Copilot, run
`copilot` and use `/model`, or pass `auto` to let Copilot pick.

## Authentication

Drivers don't manage credentials. They inherit whatever the underlying
CLI sees — your Claude Code login, your `gcloud auth` for Gemini, your
OpenCode provider config, your `copilot`/`gh` login for Copilot. Run
the CLI directly first to confirm it works, then aegis will see the
same auth.

## Adding a new driver

The driver seam is one abstract class — `HarnessDriver` in
`aegis.drivers.base` — with two methods:

- `build_argv(agent, mcp_url) -> list[str]` — argv for the subprocess.
- `start(agent, cwd, mcp_url, app_bridge) -> HarnessSession` — spawn
  and return a session object whose `send()` / `events()` /
  `cancel()` / `close()` methods speak the CLI's protocol.

If the target CLI speaks ACP, subclassing `AcpDriver` (in
`aegis.drivers.acp`) gives you all of the above for free; you only
write a 5-line shim setting `BASE_CMD`. See `gemini.py` and
`opencode.py` for examples.

## Robustness notes

A few defensive choices worth flagging if you're hacking on drivers:

- **stdin/stdout buffers** are bumped to 16 MiB to handle legitimate
  large tool-result payloads (the 64 KiB default chokes on big file
  reads).
- The ACP driver applies a small workaround for an upstream SDK race
  in `Connection.__init__` — see the top of `aegis/drivers/acp.py`.
- Driver `_wrap_error` always pulls subprocess `stderr` and any
  `acp.*` logger records into the surfaced exception, so a "harness
  error" line in the TUI always carries enough context to diagnose.
