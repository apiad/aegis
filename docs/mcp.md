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
| `aegis_list_sessions()` | Live peer sessions: handle, agent_slug, state, active, unseen, and `host`. Use this to see who you can hand off to and whether they are idle. `host` is the machine that peer's harness runs on — `"local"`, or a configured [execution host](hosts.md). |
| `aegis_list_agents()` | Configured agent profile slugs that could be spawned. |
| `aegis_spawn(agent, prompt, from_handle, …, host=None, cwd=None)` | Create a new independent top-level peer and hand it an opening prompt. Fire-and-forget: returns the new handle without waiting. `host` places its harness on another machine — see [Execution hosts](hosts.md) — where its `Bash`/`Read`/`Edit` act on *that* filesystem while it stays an ordinary peer of yours. |
| `aegis_handoff(from_handle, target_handle, context)` | One-way (fire-and-forget) context transfer to a live peer. The target receives a tagged user turn and starts working; you do not wait for its reply. |
| `aegis_title(from_handle, title)` | Give your own session a short readable title (3-8 words on what you are doing). Your **handle** stays your identity — routing key, `from_handle`, half your log id; the title is only the label a human reads. Refused when the operator set one by hand, and the refusal says so. `aegis_rename` also takes an optional `title=` to set both at once. See [Session titles](usage.md#session-titles). |
| `aegis_enqueue(queue, payload, from_handle, callback=true)` | Delegate work onto a named queue. Returns `{task_id, queued_position}`. If `callback=true`, the worker's final result arrives in your inbox later. See [Queues](queues.md). |
| `aegis_task_status(task_id)` | Inspect a previously enqueued task — useful when `callback=false` or you want to poll. |
| `aegis_fork(target_handle, …)` | Branch an **idle** peer's conversation into a new agent that already has its context. Self-fork is refused with a pointer at `/fork` — calling the tool is itself what puts you mid-turn. ~$1 a fork. |
| `aegis_peer_plan(handle)` | A peer's full task list — every task with its status and accumulated **working time** (mid-turn seconds only, so an idle agent does not inflate). `aegis_list_sessions` already carries the `done/total` roll-up and what each peer is on; this is the drill-down. |
| `aegis_read_peer(handle, turns=12)` | Read a window of a peer's transcript. It unlocks nothing (the logs are plain JSONL and every agent has Read); what it fixes is *addressing* — a log id carries the session's **birth** handle and is never renamed, so current-handle → file is not derivable. |
| `aegis_run_workflow(name, kwargs, from_handle, callback=true)` | Invoke a registered Python workflow. Non-blocking; returns `{workflow_run_id, status:'running'}` immediately. See [Workflows](workflows.md). |
| `aegis_monitor(from_handle, description, done, progress=…, fail=…)` | Watch a long-running process (tests, a build, a download) by bash condition instead of polling it. Returns `{monitor_id}` immediately — the agent ends its turn and is woken through its inbox when the condition trips, fails or times out. The result also carries `also_watching`: **the agent's other live monitors**, with elapsed time beside percent. A monitor outlives the process it watches, so one frozen at 60% for nineteen minutes is watching a corpse, and nothing else would ever tell the agent that. |
| `aegis_monitors(from_handle=None)` / `aegis_monitor_cancel(monitor_id)` | List live monitors — pass a handle to see only your own, since unscoped it lists every peer's with nothing marking ownership — or stop one. A cancel names *what it killed* (ULIDs differ in a few characters) and hands back the remaining roster, counted: cancelling is when an agent is pruning, which makes it the best moment to show the rest of the pile. Neither wakes the agent: it just made that decision. |

## Inboxes

Anything sent to an agent — by a peer, a queue callback, or the
substrate — arrives as a normal user-message turn, prefixed with a
single-line header so the agent knows where it came from:

```
> from queue:<name>   · task#<id> · ok|error · <timestamp>
> from agent:<handle> · <timestamp>
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
- Whether a message came from a **human** vs. another agent.

## Injection mechanics

For each session aegis spawns, a fresh HTTP MCP endpoint is bound:

- **Claude Code**: `--mcp-config` passes a JSON config containing one
  server (the aegis URL) per invocation.
- **Gemini / OpenCode**: `session/new(mcpServers=[{type:"http",
  name:"aegis", url:<url>, headers:[<token>]}])` injects the server when the
  ACP session opens.

### Who is calling — per-session identity

`AegisMCP` is co-resident and shared: every session on this aegis reaches the
same HTTP port, so there is no per-connection identity to read a handle off.
`from_handle` is therefore a *parameter* — each agent is told its own handle
in the primer system prompt and passes it back by convention, which means an
agent can pass a handle that is not its own.

aegis manufactures the missing identity. A **session token** is minted per
harness spawn, rides the MCP config aegis already writes (a header, never
`Authorization` — FastMCP strips that one), and is resolved server-side off
the request. Two things use it:

- **The [comms ledger](usage.md#the-aegis-layer-in-the-transcript-aegis-comms)
  attributes every call**, including the tools that take no `from_handle` at
  all (`aegis_list_sessions`, `aegis_claims`, `aegis_canvas_list`,
  `aegis_meta`, the `aegis_config_*` family) — those were recorded
  unattributed since the ledger shipped.
- **`aegis_claim` / `aegis_release` / `aegis_close` / `aegis_loop_stop` act
  on the resolved handle**, not the one the caller typed.

A rename re-points the token rather than reissuing it (the process did not
restart); a close revokes it, and a reconnect mints a fresh one. A caller
with no token — an out-of-band client, say — is recorded and rendered
unattributed exactly as before: this **resolves and records, it never
refuses**.

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
