# aegis 2 slice 3: the daily driver

**Status: implemented, 2026-10-06** (issue #129), following
`docs/superpowers/plans/2026-10-06-aegis-2-slice-3.md`. A live test runs real
Claude Code (Haiku) through the endpoint: it lists the tools, arms a monitor with
its own token, ends its turn, and is woken (14.5 s on zion). Designed with Alex; builds on
slices 1 and 2 and the vision.

## What slice 3 delivers

Agents running in aegis 2 get the aegis tools they use every day: monitors, queues
of workers, handoffs, reading a peer, listing sessions, renaming themselves, and an
inbox that delivers what those produce. With it Alex can move his daily work to
aegis 2.

The choice of tools is measured, not guessed. Over 28 days of the old aegis
(issue #129): monitors are 40% of the agents' calls to aegis, queues 29%,
rename/meta/title 15%, handoff/list/read 11%, claims 4%, everything else 1%.
This slice covers the first four, about 95%.

Out: claims, canvas, terminals, workflows, reminders, groups, `/loop`, `/btw`,
`@peer`, voice (designed separately, with a different speech model in mind).

## Decisions

| Question | Decision | Why |
|---|---|---|
| Tool names | The operation's registry name with the dot as an underscore: `monitor.start` is `mcp__aegis__monitor_start` | Claude Code already prefixes the server name; `mcp__aegis__aegis_monitor` repeated it. One rule, no second table |
| Who is calling | The per-spawn token in the MCP request header; no `from_handle` argument anywhere | Caller identity is a transport fact (DESIGN.md); an argument can lie and every call repeated it |
| When a worker is finished | Its turn ended, and it has no live monitor, no held inbox message, and no open Claude task | Ending a turn is how an agent waits; reading it as done closed a worker mid-wait in the old tree (2026-08-10) |
| A worker mid-task at a restart | Resumed at boot with "the server restarted; continue your task" | The enqueuer waits on a callback; a restart must not strand the work silently |
| Monitors and tasks across a restart | Persisted and resumed | Lazy start keeps a stopped session cheap; a monitor that fires resumes its session |

## Claude Code behaviour this relies on

- `--mcp-config '{"mcpServers":{"aegis":{"type":"http","url":…,"headers":{…}}}}'`
  with `--strict-mcp-config`, as the old tree runs it.
- Claude reports its own tasks as system notices: `task_started` with `task_id`
  and `is_backgrounded`, and `task_notification` with `task_id` and `status`
  (`completed`, `failed`). Seen in a real Haiku session on 2026-10-06. A session's
  open tasks are the ids started and not yet notified.
- A message written to stdin mid-turn is injected at the next tool boundary
  (slice 1's probe), so the inbox holds messages until a turn ends rather than
  writing them at once.

## The MCP endpoint

- `/mcp` on the same server, fastmcp's streamable HTTP app mounted under the
  Starlette app.
- Each spawn mints a token; `--mcp-config` carries it as `X-Aegis2-Session`.
  The endpoint resolves the token to the calling session; a call with no valid
  token is refused. The token map is in memory and re-minted when a stopped
  session's process starts again.
- The tools are the registry operations marked for agents. Each takes the
  operation's pydantic params; the handler receives the params and the caller.
- `--append-system-prompt` adds a short primer: the agent's handle and server, that
  messages arrive as user turns headed `> from <kind>:<name> · …`, that waiting on
  something means arming a monitor and ending the turn, and that a worker's last
  prose is its result.

## The inbox

- `deliver(session, header, body)`: a session that is idle gets the message as a
  prompt at once; a working one holds it and gets every held message as one
  prompt when its turn ends; a stopped one is resumed to receive it. An archived
  session refuses delivery (the sender is told).
- The prompt is the messages in arrival order, each `> from <kind>:<name> · <ts>`
  then its body, separated by a blank line.
- The fold renders a user entry whose text starts with `> from ` as kind `inbox`,
  glyph `⇄`, its first header line as the title.
- Held messages are kept in the session's meta, so a restart does not drop them.

## Monitors

- `monitor.start(description, done, fail?, progress?, interval_s=10,
  timeout_s=3600, cwd?)` returns `{monitor_id}`. Conditions are bash, run with
  `bash -c` in the session's cwd or `cwd` (relative to it), each with a 30 s limit.
  Exit 0 of `done` completes it, exit 0 of `fail` fails it, `progress` prints
  0 to 100.
- When it ends (done, failed, timed out, or a condition that cannot run), the
  owner gets `> from monitor:<id> · ok|fail|timeout · <ts>` with the description,
  the elapsed time, and the owner's other live monitors listed so it can cancel
  stale ones.
- `monitor.cancel(monitor_id)` (own monitors only), `monitor.list()` (own).
- Monitors are kept in `<state>/monitors.json` and re-armed at boot. A monitor
  whose owner is archived is dropped.
- The session meta carries its monitors (`id`, `description`, `progress`); the
  sidebar shows them with bars, the Fleet card shows the count.

## Queues

- Read-only from `.aegis.yaml`'s `queues:` map: name, `agent` (a profile),
  `max_parallel`.
- `queue.enqueue(queue, payload, callback=true)` returns `{task_id,
  position}`. A task waits FIFO for a free slot, then a worker session is spawned
  from the queue's profile in the enqueuer's cwd, with the payload as its first
  prompt. The worker's meta records `worker: {task_id, queue}`; its card says
  "worker".
- **Finished** means the worker's turn ended with no live monitor, no held inbox
  message and no open Claude task. Then the task is `completed` with the
  worker's last prose as its result; with `callback`, the enqueuer gets
  `> from queue:<name> · task#<id> · ok · <ts>` and the result; the worker is
  archived.
- **Failed** means the worker's process exited on its own; the callback says
  `error` with the exit and stderr tail; the worker's tab stays for inspection.
- `task.status(task_id)`, `task.cancel(task_id)` (pending: dropped; running:
  interrupted and archived), `task.resume(task_id)` (a failed task's worker gets
  "continue your task" and the task is running again).
- Tasks are an append-only log, `<state>/tasks.jsonl`; boot replays it. Pending
  tasks are dispatched; a running task's worker is resumed with "the server
  restarted; continue your task".

## The small tools

- `peer.handoff(target, context)`: delivers `> from agent:<caller> · <ts>` and
  the context to the target's inbox.
- `peer.read(target, last=30, tools=false)`: the target's last entries as text,
  one line per entry (`you:`, `agent:`, `from …:`), tool rows only with `tools`.
- `session.list()`: open sessions with handle, title, state, cwd, and worker.
- `session.rename(handle?, title?)`: the caller's own session.
- `meta()`: what the primer says, and the tools.

Agents can change only what they created: their own monitors, their own session's
names, tasks they enqueued. Reading and messaging are open.

## The client

- Inbox rows: `⇄`, the sender and kind as title, the body as Markdown.
- Calls to aegis tools: `mcp__aegis__` stripped, glyph `⇄`, the verb as title and
  a one-line label (a monitor's description, a queue and the first line of its
  payload, a handoff's target). The reply is digested to the id it made, the
  status, or a count. (Added during the build: the raw JSON reply was unreadable.)
- Sidebar: a Monitors section with a bar per live monitor.
- Fleet card: the monitor count, a "worker" badge with the queue.

## Testing

Lesson tests first:

- a turn ending with a live monitor, a held message, or an open Claude task does
  not finish a worker;
- a message to a working session is held and delivered once, at the turn end,
  batched with others;
- a tool call without a valid token is refused, and a call with one acts as that
  session;
- monitors and tasks survive a restart; a running task's worker resumes;
- an agent cannot cancel another session's monitor or task.

The fake claude learns to call MCP tools: a prompt `/mcp <tool> <json>` makes it
POST a `tools/call` to the URL and header in its `--mcp-config`, and print the
result as text, so end-to-end tests go through the real endpoint.

A live test: real Haiku arms a monitor on a file that a test creates, ends its
turn, and is woken.

## Done means

1. `make check`, browser tests and the bench pass.
2. Alex's daily flow works in aegis 2: an agent arms a monitor and is woken, a
   producer enqueues to `general` and gets the callback, two agents hand off.
3. DESIGN.md's aegis 2 part describes the endpoint, the inbox and the completion
   rule.
4. A changelog fragment.
