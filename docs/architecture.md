# Architecture & Vision

Aegis is a **meta-harness**: it does not call model APIs. It drives
existing coding-agent CLIs (Claude Code, Gemini CLI, OpenCode) as
subprocesses and adds a control plane above them — multi-agent
multiplexing, an inbox-based routing fabric, queues, workflows, and an
MCP server every spawned agent talks to.

## Layering

```
┌────────────────────────────────────────────────────────┐
│  Front-ends:  TUI (Textual)     Telegram bot          │
│               │                  │                     │
├───────────────┼──────────────────┼─────────────────────┤
│  Core:        SessionManager  ←→ InboxRouter          │
│                                                         │
│               Workflows  ←→  QueueManager              │
├─────────────────────────────────────────────────────────┤
│  Drivers:     ClaudeDriver   GeminiDriver   OpenCodeDriver
│                  │              │              │       │
│                  │ stream-json  │ ACP          │ ACP   │
│                  ▼              ▼              ▼       │
│              claude -p      gemini --acp   opencode acp │
├─────────────────────────────────────────────────────────┤
│  MCP plane:   AegisMCP (HTTP, per-session-bound URL)   │
│               injected into every spawned agent        │
└─────────────────────────────────────────────────────────┘
```

### Drivers

`drivers/` is the seam to one specific coding-agent CLI. Each driver
spawns a subprocess, sends user messages, and yields a stream of typed
events: `AssistantText`, `AssistantThinking`, `ToolUse`, `ToolResult`,
`Result`, etc. Above the driver, aegis treats every provider the same.

Two protocol families:

- **stream-json** (Claude Code) — bidirectional JSON-per-line over
  stdin/stdout. User messages go in as `user_message` frames; events
  come out as `*_event` frames.
- **ACP** (Gemini, OpenCode) — JSON-RPC over stdio per the
  [agent-client-protocol](https://github.com/zed-industries/agent-client-protocol)
  spec. Multi-turn via `session/prompt`, MCP injection via the
  `session/new` request.

See [Drivers](drivers.md).

### Events & rendering

A typed event stream (`events.py`) and a pure `render_event(ev) →
Rich renderable | None` mapping (`render.py`). The TUI never knows
about subprocess output; it only knows about events.

### Core

`core/` carries the harness-agnostic session core:

- `AgentSession` — turn loop, metrics, state, observer callbacks.
- `SessionManager` — owns N sessions, hands them out by handle,
  bridges Textual's async lifecycle with the rest of the substrate.

Both the TUI pane and the Telegram bot delegate to `SessionManager`.

### Inboxes & queues

`queue/` carries the InboxRouter (per-handle delivery; wake-on-idle /
mid-turn buffer / turn-end chain) and the QueueManager (FIFO +
max-parallel cap + substrate-deterministic dispatch + JSONL lifecycle
log). Universal sender tagging: queue callbacks, peer handoffs,
Telegram, and the substrate all arrive at agent inboxes through one
channel with a consistent `> from <sender> · …` header.

See [Queues](queues.md).

### Workflows

`workflow/` adds Python-procedure orchestration on top of queues +
sessions. A `@workflow` decorator registers async functions; the
`WorkflowEngine` runtime gives them `spawn` / `send` / `drain` /
`delegate` / `close` / `bash` / `log` against the live substrate.
`runner.run_workflow` handles auto-drain (touched handles) and
auto-close (spawned handles) on exit.

See [Workflows](workflows.md).

### MCP plane

`mcp/` is a FastMCP HTTP server owned by aegis. Each spawned agent
gets injected with a per-session URL that tags every tool call with
the agent's own handle. The server exposes orientation
(`aegis_meta`), peer discovery (`aegis_list_sessions`,
`aegis_list_agents`), handoff (`aegis_handoff`), queue dispatch
(`aegis_enqueue`, `aegis_task_status`), and workflow invocation
(`aegis_run_workflow`).

See [MCP plane](mcp.md).

### TUI

`tui/` is the Textual app. One `ConversationPane` per agent session;
N panes in a `ContentSwitcher`; a sideways-scrolling tab bar; an
agent-picker modal; cross-tab signalling (state dot, sticky `*`,
bell); per-block copy-to-clipboard; an inline working indicator
(spinner + rotating verb + elapsed timer) mounted inside the
transcript.

## Design principles

- **Above, not in.** Aegis never replaces the underlying agent. If the
  upstream CLI gets better, aegis automatically does too.
- **No log scraping.** Every signal — tokens, tool calls, metrics — is
  read from a structured protocol (stream-json or ACP). The TUI never
  reads stdout text and tries to parse it.
- **Same UX across providers.** Adding a new provider is a 5-line
  shim if it speaks ACP. Adding a non-ACP provider is a single
  `HarnessDriver` subclass.
- **Substrate-deterministic.** Queues dispatch synchronously on event;
  no background loop. Restart replay is JSONL. The TUI ↔ substrate
  bridge is an explicit adapter, not a thread.
- **Honest metrics.** True input including cache; cached %; per-turn
  and per-session timing — all from the harness's own accounting.

## Vision

The roadmap runs from a single-tab CLI (Phase 1) through multi-tab
TUI (Phase 2), MCP plane (Phase 3), queues and workflows (Phase 4),
multi-provider parity via ACP (current phase), and onward to
multi-host distribution and richer orchestration primitives. See
[Roadmap](roadmap.md).

Aegis is personal-infrastructure-grade and evolves quickly. The
seams are designed to absorb upstream protocol changes without
churning the TUI or the workflow API.
