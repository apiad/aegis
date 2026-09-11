# Drivers

A **driver** is the layer that owns one coding-agent CLI subprocess.
It speaks the CLI's structured protocol, sends user messages, and
yields typed events (`AssistantText`, `ToolUse`, `ToolResult`,
`Result`, etc.) to the surrounding session. Above the driver, aegis
treats every provider identically.

Four drivers ship today: `claude-code`, `gemini`, `opencode` and
`lovelaice`. All four give the same UX surface — multi-turn, streaming,
cancellation, per-session MCP injection.

The first three wrap a coding-agent CLI you installed yourself.
`lovelaice` is the odd one out and the reason the sentence above says
"driver" rather than "CLI wrapper": it is the **native, harness-free**
agent. `lovelaice` is a PyPI dependency of aegis, so a fresh install has
a working agent with no external CLI in the loop — point it at a local
endpoint for local models, or give it a key for a direct API.

## How drivers talk to each CLI

| Provider | Protocol | Mode | Per-session MCP | OAuth |
|---|---|---|---|---|
| Claude Code | stream-json (bidirectional) | `claude -p` with `--input-format/--output-format stream-json` | `--mcp-config` per invocation | Native |
| Gemini CLI  | [ACP](https://github.com/zed-industries/agent-client-protocol) | `gemini --acp` | `session/new(mcpServers=[…])` | Pass-through |
| OpenCode    | ACP | `opencode acp` | `session/new(mcpServers=[…])` | Pass-through |
| Lovelaice   | ACP | `lovelaice-acp` (a dependency, not a CLI you install) | `session/new(mcpServers=[…])` | None — key file, or none for a local endpoint |

ACP (Agent Client Protocol) is Zed's JSON-RPC-over-stdio specification
for editor↔agent communication. Aegis uses the official Python SDK
[`agent-client-protocol`](https://pypi.org/project/agent-client-protocol/)
to drive Gemini, OpenCode and Lovelaice through it.

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

## One-shot generation

Beside the session seam, a driver may declare `supports_oneshot` and implement
`generate()` — a single prompt in, a string out, with **no session, no MCP and
no tools**. It backs aegis's own small calls (`/btw`, and the `@peer` teaser's
neighbours); which model pays is the
[`text_generation:`](configuration.md#text_generation-optional) config key.

The tool-shedding is the point, not an optimisation. Measured on zion with the
same window and question, on haiku:

| Invocation | Time | Cost | Input | Result |
|---|---|---|---|---|
| default `claude -p` | 21.9s | $0.0633 | 53,593 tok | refusal |
| `--system-prompt` + `--tools ""` | 8.5s | $0.0044 | 2,361 tok | correct |

`claude -p` with default flags is not a generator, it is an agent, and it
behaves like one: the default run went looking for files a side note has no
business reading, failed to find them, and answered "I cannot verify" — the
worst outcome, at fifteen times the price. `--tools ""` sheds the tool schemas
(most of those 53k input tokens) and with them the urge to use them;
`--system-prompt` replaces claude's agentic default, which
`--append-system-prompt` cannot do.

## Picking models

Each provider's `model` string is whatever its native CLI accepts:

| Provider | Examples |
|---|---|
| `ClaudeCode` | `opus`, `sonnet`, `haiku` |
| `GeminiCLI`  | `gemini-3-flash-preview`, `gemini-3.1-pro-preview` |
| `OpenCode`   | `opencode/kimi-k2.6`, `opencode/glm-5.1`, `opencode/minimax-m2.7`, `opencode/qwen3.6-plus` |
| `Lovelaice`  | whatever the endpoint accepts — an OpenRouter id like `anthropic/claude-haiku-4-5`, or a local model id served by Ollama |

For OpenCode, run `opencode models` to see what's installed on your
machine. For Gemini, see Google's model docs. For Lovelaice the answer
depends entirely on `base_url`, since the model string is handed to that
endpoint unchanged.

## Authentication

The three CLI drivers don't manage credentials. They inherit whatever
the underlying CLI sees — your Claude Code login, your `gcloud auth` for
Gemini, your OpenCode provider config. Run the CLI directly first to
confirm it works, then aegis will see the same auth.

`lovelaice` is the exception, because there is no CLI underneath it to
have logged in. It reads a key from the path in `api_key_file` **at
spawn** and injects it into the subprocess environment. Give it a scoped
file path; never inline a key in `.aegis.yaml`. Against a local endpoint
there is nothing to authenticate and `api_key_file` can be omitted
entirely. See [Agents](configuration.md#agents).

## Adding a new driver

The driver seam is one abstract class — `HarnessDriver` in
`aegis.drivers.base` — with two abstract methods:

- `build_argv(agent, cwd, mcp_url, handle) -> list[str]` — argv for the
  subprocess.
- `session(agent, cwd, mcp_url, handle, launcher, token) -> HarnessSession`
  — spawn and return a session object whose `send()` / `events()` /
  `cancel()` / `close()` methods speak the CLI's protocol.

Three optional capability flags default to `False` and gate features that
are not universal: `supports_resume`, `supports_fork` and
`supports_oneshot` (the last one is [one-shot
generation](#one-shot-generation) above). Override `resume()` / `fork()`
/ `generate()` alongside the flag you set.

If the target CLI speaks ACP, subclassing `AcpDriver` (in
`aegis.drivers.acp`) gives you all of the above for free; you only
write a 5-line shim setting `BASE_CMD`. See `gemini.py` and
`opencode.py` for examples. `lovelaice.py` is the same shim plus one
more seam worth knowing about: `extra_env(agent) -> dict[str, str]`, for
a CLI configured by environment rather than by flags. It is how the
model, endpoint and key reach `lovelaice-acp`, and it is also how
OpenCode's model gets set, since `opencode acp` has no `-m` flag.

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
