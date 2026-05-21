# MCP plane

Every agent that aegis spawns is automatically injected with a
per-session **MCP server** owned by aegis. That server exposes the
substrate to the agent — who its peers are, how to delegate, how to
hand off context — and tags every call with the agent's own handle so
routing is deterministic.

The injection is **strict**: aegis tells the underlying CLI to load
*only* the aegis MCP server. Other MCP servers from the user's global
config are not loaded inside aegis sessions, so the agent's tool
surface is exactly what aegis declares.

## What's on offer

| Tool | Purpose |
|---|---|
| `aegis_meta()` | Self-orientation briefing — what aegis is, what tools are available, how the inbox works. Always the first tool a new agent should call. |
| `aegis_list_sessions()` | Live peer sessions: handle, agent_slug, state, active, unseen. Use this to see who you can hand off to and whether they are idle. |
| `aegis_list_agents()` | Configured agent profile slugs that could be spawned. |
| `aegis_handoff(from_handle, target_handle, context)` | One-way (fire-and-forget) context transfer to a live peer. The target receives a tagged user turn and starts working; you do not wait for its reply. |
| `aegis_enqueue(queue, payload, from_handle, callback=true)` | Delegate work onto a named queue. Returns `{task_id, queued_position}`. If `callback=true`, the worker's final result arrives in your inbox later. See [Queues](queues.md). |
| `aegis_task_status(task_id)` | Inspect a previously enqueued task — useful when `callback=false` or you want to poll. |
| `aegis_run_workflow(name, kwargs, from_handle, callback=true)` | Invoke a registered Python workflow. Non-blocking; returns `{workflow_run_id, status:'running'}` immediately. See [Workflows](workflows.md). |

## Inboxes

Anything sent to an agent — by a peer, a queue callback, the substrate,
or the Telegram front-end — arrives as a normal user-message turn,
prefixed with a single-line header so the agent knows where it came
from:

```
> from queue:<name>   · task#<id> · ok|error · <timestamp>
> from agent:<handle> · <timestamp>
> from telegram       · <timestamp>
> from workflow:<name> · task#<id> · ok|error · <timestamp>
```

Multiple messages that arrive while an agent is mid-turn batch into a
single user turn at the next turn boundary; each entry keeps its own
header. If the agent was idle, an arrival wakes it into a fresh turn
automatically.

## Sender tagging

Every message that flows through the substrate carries a `SenderTag`
that uniquely identifies its origin (agent handle, queue + task, or
external front-end). The `> from …` header is rendered from that tag.
This means an agent looking at its own inbox can always tell:

- Which **peer** sent it a handoff.
- Which **queue** a callback is for, and whether the worker succeeded.
- Whether a message came from a **human** via Telegram vs. another agent.

## Injection mechanics

For each session aegis spawns, a fresh HTTP MCP endpoint is bound:

- **Claude Code**: `--mcp-config` passes a JSON config containing one
  server (the aegis URL) per invocation.
- **Gemini / OpenCode**: `session/new(mcpServers=[{type:"http",
  name:"aegis", url:<url>, headers:[]}])` injects the server when the
  ACP session opens.

The URL embeds the session's handle, so every tool call automatically
carries `from_handle` even if the agent doesn't pass it explicitly.

## Building on the plane

Anything new aegis wants to expose to agents goes through the MCP
plane. Adding a tool is a matter of:

1. Define the tool function in `aegis.mcp.server` (it's a FastMCP
   server).
2. Document it in the `BRIEFING` and `PRIMING` strings so newly
   spawned agents discover it via `aegis_meta()`.
3. If the tool needs substrate access, take it off the `AppBridge`
   handle that the server holds.

Workflows are the higher-level alternative: if "the new thing" is a
fixed Python procedure that drives existing tools, just write a
workflow instead — no new MCP surface needed.
