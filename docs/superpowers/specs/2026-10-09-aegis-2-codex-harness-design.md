# Codex sessions in aegis 2

> **Status:** design, not implemented, 2026-10-09.
> Issue: [#105](https://github.com/apiad/aegis/issues/105).
> Plan: `docs/superpowers/plans/2026-10-09-aegis-2-codex-harness.md`.
> Template: `2026-10-08-aegis-2-opencode-harness-design.md`, whose harness
> interface this spec reuses unchanged.

An agent whose harness is `codex` spawns from the new tab and behaves like a
Claude or OpenCode session. Its transcript streams token by token, with the same
glyphs and tool rows. It takes prompts, mid-turn prompts, interrupts, slash
commands and inbox messages, and it resumes after a server restart. Its model,
effort and permission change while it runs, without restarting the process. It
calls every aegis tool over MCP and can be a queue worker. Its card shows
context and cost, and the Usage panel shows the ChatGPT plan's windows next to
Claude's and OpenCode Go's.

The interface in `harness.py` already fits. This spec adds a third module behind
it, `src/aegis/codex/`, and touches the places that name a harness (listed at the end).

## What Codex does

Measured on `codex-cli 0.162.1` on zion, against a free OpenRouter model
(`nvidia/nemotron-3-super-120b-a12b:free`, with `cohere/north-mini-code:free`
for the model switch), so no OpenAI account was involved. The probes, the
generated protocol schema and the raw JSON-RPC lines are in the Workspace at
`.playground/codex-free/` (`probe.py`, `probe_resume.py`, `probe2.py`,
`probe3.py`, `FINDINGS.md`).

| Question | Answer |
|---|---|
| Which interface runs a turn | `codex app-server`: JSON-RPC 2.0 over stdio, one JSON object per line. `initialize` then the `initialized` notification, then `thread/start` (or `thread/resume`) and `turn/start`. `codex app-server generate-json-schema` prints the whole protocol: 105 client methods, 84 notifications, 10 server-to-client requests. |
| Start cost | 0.24 s from spawn to the `initialize` answer. 225 MB resident: 44 MB for the npm `codex` wrapper (node) and 181 MB for the native binary it starts. A `claude` process is 311 to 343 MB, an `opencode serve` 310 MB. |
| What a turn emits | `turn/started`, then `item/started` and `item/completed` per item, `item/agentMessage/delta` and `item/reasoning/textDelta` while text streams, `thread/tokenUsage/updated` after each model request, `account/rateLimits/updated`, and `turn/completed` with a `status`. Three turns produced 1,193 lines; 984 of them were reasoning deltas. |
| Items | 19 kinds: `userMessage`, `agentMessage`, `reasoning`, `commandExecution`, `fileChange`, `mcpToolCall`, `dynamicToolCall`, `collabAgentToolCall`, `subAgentActivity`, `webSearch`, `imageView`, `imageGeneration`, `plan`, `contextCompaction`, `sleep`, `hookPrompt`, `functionCallOutput` and the two review-mode markers. An `item/completed` carries the whole item, final text included. |
| Approvals | Under `approvalPolicy: "untrusted"` every command arrives as an `item/commandExecution/requestApproval` request; answering `{"decision": "accept"}` lets it run. Under `"never"` nothing asks. |
| Mid-turn prompt | `turn/steer {threadId, expectedTurnId, input}` during a running `sleep 15`: accepted, one turn, two `userMessage` items, and the final answer used the steered text. |
| Interrupt | `turn/interrupt` during a running `sleep 60`: `turn/completed` with status `interrupted` at once. |
| Resume | After closing stdin (exit 0), a new process's `thread/resume` worked within 1 s and the model recalled the earlier turns. After SIGKILL, resume failed for 180 s and more with "already has an active writer". Every start also launched a plugin-marketplace `git fetch` that outlived the server; killing those orphans made the resume work. With `--disable plugins --disable remote_plugin` no child was left in the process group. |
| System prompt | `developerInstructions` on `thread/start` is kept with the thread: a resumed thread in a new process still followed it. |
| Per-turn settings | `turn/start` takes `model`, `effort`, `sandboxPolicy` and `approvalPolicy`. A turn on `cohere/north-mini-code:free` in a thread started on nemotron ran on cohere (the rollout's `turn_context` says so), and a `workspaceWrite` turn wrote a file that the `readOnly` turn before it could not. |
| MCP | `-c mcp_servers.aegis.url=…` plus `env_http_headers={"X-Aegis-Session"="AEGIS_SESSION_TOKEN"}` connected to a FastMCP stand-in, and the call carried the token taken from the child's environment. Under `approvalPolicy: "never"` the call failed with "MCP tool call requires approval, but approval policy is never" until the server also had `default_tools_approval_mode="approve"` (valid values: `auto`, `prompt`, `writes`, `approve`). |
| Sandbox | `read-only` refused `echo hi > x.txt` with "Read-only file system". `workspace-write` allowed it. Both ran through Codex's Linux sandbox with no setup. |
| Subagents | With `multi_agent` on (the default), the model called `spawnAgent` and `wait` as `collabAgentToolCall` items naming the child's thread id, and the child thread's own `turn/*` and `item/*` events arrived on the same stream under its `threadId`. |
| Background commands | `(sleep 20; echo done > z.txt) &` returned at once and never wrote the file: Codex ends a command's process group when the command item completes. No event arrived after the turn ended. |
| Usage | `thread/tokenUsage/updated` gives `total` and `last` breakdowns (`inputTokens`, `cachedInputTokens`, `cacheWriteInputTokens`, `outputTokens`, `reasoningOutputTokens`, `totalTokens`) and `modelContextWindow` (258,400, Codex's fallback for a model it has no metadata for). No event carries a price. |
| Quota | `account/rateLimits/read` returns `primary` and `secondary` windows (`usedPercent`, `windowDurationMins`, `resetsAt`), `planType` and `credits` for a ChatGPT login. With an API-key provider it fails with "codex account authentication required to read rate limits", and `account/rateLimits/updated` arrives after each request with every field null. |
| Models | `model/list` lists Codex's own catalog for the `openai` provider (8 models on this version, `gpt-5.5` to `gpt-6.1-sol`) with `supportedReasoningEfforts` and a default effort. It lists nothing for another provider. |
| Commands | `skills/list` lists 5 bundled skills plus the user's. A skill is invoked with a `{"type": "skill", …}` input item. `/compact` is `thread/compact/start` (a `contextCompaction` item in a turn of its own) and `/review` is `review/start`. |
| Titles | No `thread/name/updated` arrived in 7 turns. |
| Warnings | A `warning` notification per request when Codex lacks the model's metadata, and one after compaction ("Long threads and multiple compactions…"). |

## Choices

**`codex app-server`, not `codex exec`, not `codex mcp-server`.** `codex exec
--json` is one process per prompt. A prompt cannot reach a running turn, an
interrupt has to kill the process, and stdin must be `/dev/null` or it waits
for input after the prompt. `codex mcp-server` exposes a turn as one tool call,
so nothing streams. app-server is what OpenAI's own IDE extension and desktop
app drive, so it gets new features first, and every feature in the table
above has a method behind it. aegis pins the protocol with the generated schema
and a live test, as it pins OpenCode's v1 routes.

**One app-server per session, as for Claude and OpenCode.** One app-server can
hold many threads, and `thread/start` even takes a per-thread `config`. But the
MCP token is how aegis knows who is calling, and per-process configuration is
the one place it cannot be crossed between sessions. The process model also
stays the same: a child per live session, ended by Stop, Close and shutdown.

**The child runs in its own process group and stops by closing stdin.** A clean
exit releases the thread's writer lease; SIGKILL left it held by a surviving
grandchild. `terminate` closes stdin and waits 5 s, then sends SIGTERM to the
group, then SIGKILL to the group. After any exit, a stop or a crash, the
process kills whatever is left in the group, so a grandchild of a dead child
cannot hold the lease. The plugin and remote-plugin features are disabled on
the command line, so nothing clones a marketplace on every spawn.

**Nothing asks.** aegis has no approval card yet (the OpenCode spec left it to
its own issue), so every turn runs with `approvalPolicy: "never"` and the
aegis MCP server with `default_tools_approval_mode="approve"`. Codex has the
richest approval protocol of the three harnesses (typed requests per command,
file change, permission and MCP elicitation, with `acceptForSession`), so it is
the natural first client of that future card.

**The token reaches the child through its environment.** `http_headers` on the
command line would put the token in `ps` output for every user on the machine.
`env_http_headers` names an environment variable instead. Codex's default
`shell_environment_policy` passes that variable to the shells the model runs
(`env | grep -c AEGIS_SESSION_TOKEN` printed 1), so the command line also sets
`shell_environment_policy.exclude=["AEGIS_SESSION_TOKEN"]`; with it the shell
saw 0 and the MCP call still carried the token.

**The user's own `CODEX_HOME`.** The child uses `~/.codex` (or `$CODEX_HOME`),
so it shares the login, the providers, the skills and the AGENTS.md handling
with the person's own Codex. aegis adds its settings with `-c` and never writes
the file.

## Design

### The Codex process

`src/aegis/codex/process.py` starts:

```
codex app-server
  --disable plugins --disable remote_plugin
  -c mcp_servers.aegis.url="<aegis /mcp URL>"
  -c mcp_servers.aegis.env_http_headers={"X-Aegis-Session"="AEGIS_SESSION_TOKEN"}
  -c mcp_servers.aegis.default_tools_approval_mode="approve"
  -c shell_environment_policy.exclude=["AEGIS_SESSION_TOKEN"]
  -c approval_policy="never"
```

in the session's cwd, with `AEGIS_SESSION_TOKEN` in its environment and
`start_new_session=True`. A session without aegis (`launch.mcp is None`) drops
the three `mcp_servers` flags and the variable. stdout is read line by line on a task; stderr
goes to `launch.stderr_path` with the last 20 lines kept for the exit report,
as `OpenCodeProcess` does.

A small JSON-RPC client inside the process object numbers requests, matches
responses by id, and routes notifications to `on_line`. It answers any
server-to-client request at once: approvals with `decline` (none should come
under `never`), `item/tool/requestUserInput` and MCP elicitations with a cancel,
and logs each one to stderr, because a request nobody answers would hang the
turn.

`start` sends `initialize` (client name `aegis`, its version) and `initialized`,
then one of:

- `thread/start {cwd, model, modelProvider, sandbox, approvalPolicy: "never",
  developerInstructions}`, where `developerInstructions` is the aegis primer
  plus the agent's priming;
- `thread/resume {threadId: resume_id, cwd, model, modelProvider, sandbox,
  approvalPolicy: "never", developerInstructions}`. Passing the primer again
  keeps an upgraded aegis's primer current on old threads.

If the resume fails because Codex no longer has the thread (error -32600, "no
rollout found for thread id …", measured with a made-up id), the process starts
a new one and the session records a `reset` record, shown as "Codex no longer
had this conversation; it started a new one", the same path OpenCode uses. Any
other resume failure ends the child and raises, and the registry reports it.

`session_id` is the thread id. The process also remembers the running turn's
id from `turn/started` and clears it at `turn/completed`.

### What is stored, and what is not

The store keeps app-server notifications as `{"src": "codex", "line": …}`:
`turn/started`, `turn/completed`, `item/started`, `item/completed`,
`thread/tokenUsage/updated`, `thread/name/updated` and `turn/plan/updated`,
for the session's thread and for the child threads its `spawnAgent` calls
name. Four kinds of line are the process's own, written through `on_line` as
`{"method": "aegis/<kind>", "params": …}` so the parser reads one shape, for
what only a response or aegis itself knows:

- `aegis/initialize`: the `initialize` answer, whose `userAgent` holds the Codex
  version;
- `aegis/thread`: the `thread/start` or `thread/resume` answer;
- `aegis/turn`: the model, effort and permission a turn was sent with, because
  no notification names a turn's model and the price depends on it;
- `aegis/command`: a slash command's line as typed.

Deltas (`item/agentMessage/delta`, `item/reasoning/textDelta`,
`item/reasoning/summaryTextDelta`) are folded live and never stored, exactly
as OpenCode's `message.part.delta`: the `item/completed` that closes the item
carries the whole text. Storing them would have kept 984 of 1,193 lines in the
probe. Everything else is dropped: status changes, `warning` (the
missing-metadata warning repeats on every request of a model Codex does not
know), `mcpServer/*`, `account/rateLimits/updated`, `remoteControl/*` and
`skills/changed`.

### From notifications to entries

`src/aegis/codex/stream.py` holds a stateful `Parser`, registered in
`transcript.entries.PARSERS` under `"codex"`, which maps lines onto the events
the fold already handles.

| Codex line | aegis event |
|---|---|
| `aegis/initialize` and `aegis/thread/start` (or `aegis/thread/resume`) | `Init(session_id=thread id, model="provider/model", version, harness="Codex")` |
| `item/completed` `userMessage` of this thread | `Echo(text)` |
| `item/started` or `item/completed` `agentMessage` | `Text(text, key=item id)` |
| `item/completed` `reasoning` | `Thinking(summary or content, key=item id)` |
| `item/agentMessage/delta`, `item/reasoning/textDelta` | `Delta(key=item id, text)`, live only |
| `item/started` `commandExecution` | `ToolCall(id, "Bash", {"command": …})` |
| `item/completed` `commandExecution` | `ToolOutput(id, aggregatedOutput, is_error=exitCode != 0 or status failed)` |
| `item/started` `fileChange` | one `ToolCall` per change: `Edit` with the unified diff for `update`, `Write` for `add`, a `Bash`-style `rm` row for `delete` |
| `item/completed` `fileChange` | `ToolOutput` with the patch status |
| `item/started` `mcpToolCall` | `ToolCall(id, "mcp__<server>__<tool>", arguments)` |
| `item/completed` `mcpToolCall` | `ToolOutput(id, result text or error, is_error)` |
| `webSearch` | `ToolCall` and `ToolOutput` of `WebSearch` |
| `collabAgentToolCall` `spawnAgent` | `ToolCall(id, "Task", {"prompt": …})`; its `receiverThreadIds` become children whose events carry `parent=id` |
| `collabAgentToolCall` `wait`, `sendInput`, `closeAgent` | a `ToolCall`/`ToolOutput` pair under the Codex tool name |
| `turn/plan/updated` | `ToolCall` of `TodoWrite` with the steps |
| `item/completed` `contextCompaction` | `Compact(pre_tokens, post_tokens)` from the usage around it |
| `thread/tokenUsage/updated` | `Step(Usage)` for the context gauge |
| `aegis/turn` naming a model other than the last | `Init` with that model |
| `aegis/command` | `Echo(line)`; the turn's own `userMessage` is then not echoed |
| `thread/name/updated` with a name | `Title(text)` |
| `turn/completed` | `Result(is_error, subtype, duration_ms, cost_usd, context_window)` |

`Usage` from a `last` breakdown is `input = inputTokens - cachedInputTokens`,
`cache_read = cachedInputTokens`, `cache_creation = cacheWriteInputTokens`,
`output = outputTokens` (reasoning tokens are already inside it: the probe's
total was 64,138 input plus 2,912 output, of which 2,754 were reasoning), so
`Usage.context` equals Codex's `last.totalTokens`. `Result.context_window` is
the last `modelContextWindow`. `duration_ms` is the turn's `durationMs`. Status
`interrupted` sets the fold's interrupt flag; `failed` sets `is_error` with the
turn's `error` as subtype.

Tool names and inputs are translated in the parser, as for OpenCode, so
`describe.py` keeps one vocabulary. The stored line keeps Codex's own names.
`mcp__aegis__<op>` already matches what the fold expects for aegis tools.

### Sending, steering, commands and interrupting

`send(text)` with no turn running is `turn/start {threadId, input: [text],
model, effort, sandboxPolicy}`. The session moves to `working` on send.

A prompt sent while a turn runs is `turn/steer {threadId, expectedTurnId,
input}`, read at the next model request, as Claude reads a prompt at the next
tool boundary. If the turn ended between the check and the call, Codex refuses
the precondition and `send` falls back to `turn/start`. The inbox keeps holding
its messages until the turn ends; nothing about it changes.

`/name args`, where `name` is in the session's catalog:

- a skill becomes `turn/start` with a `{"type": "skill", "name", "path"}` item
  followed by the arguments as text;
- `compact` is `thread/compact/start`;
- `review` is `review/start` on the uncommitted changes, with the arguments as
  instructions.

The echo of a skill turn is its `userMessage`, which the existing fold rule
("a command's echo shows the line as typed") matches to the pending `/` line.
aegis's own commands are resolved before this, as today.

`interrupt` is `turn/interrupt {threadId, turnId}`. The turn ends through the
`turn/completed` that follows, and the existing deadline marks the session
`error` if it never comes.

### Model, effort and permission

A Codex model in aegis is `provider/model`, as OpenCode's is: the first segment
is a Codex provider id (`openai`, `ollama`, `lmstudio`, or a key of the user's
`model_providers`), and the rest is the model id, slashes included. So
`openai/gpt-5.5` and `openrouter/nvidia/nemotron-3-super-120b-a12b:free`.
`agents.py` applies the rule it already applies to OpenCode: a `codex` agent
whose model has no `/` is reported by name.

All three settings travel with each `turn/start`, so `set` stores the value and
the next turn carries it, with no request and no restart:

- `model`: a new model within the same provider goes in `turn/start.model`. A
  new provider is the one case that restarts the child: the process ends it
  after the turn and resumes the thread with the new `modelProvider`, inside
  the process object, as OpenCode's permission change does.
- `effort`: `turn/start.effort`, validated against the model's
  `supportedReasoningEfforts` when `model/list` knows the model. A model it does
  not know takes no effort and none is sent, the OpenCode rule for a model
  without variants.
- `permission`: `turn/start.sandboxPolicy`.

| aegis | Codex sandbox policy |
|---|---|
| `read` | `{"type": "readOnly", "networkAccess": false}` |
| `write` | `{"type": "workspaceWrite", "networkAccess": false}` |
| `auto` | `{"type": "workspaceWrite", "networkAccess": true}` |
| `full` | `{"type": "dangerFullAccess"}` |

Each level allows strictly more than the one before, so an agent spawning a
session still cannot gain power. `read` differs from OpenCode's `read`: Codex
runs shell commands in a read-only sandbox instead of refusing them, which still
writes nothing. The live test checks that a `read` session's write fails.

### The catalog

`Codex.probe` and a live process's `catalog` ask `model/list` and `skills/list`.
A model from `model/list` becomes `openai/<id>`, labelled with its
`displayName`, with its `supportedReasoningEfforts` as efforts. For any other
provider the catalog also lists that provider's own `/models`: `config/read`
gives its `base_url` (measured: `config.model_providers.openrouter.base_url`),
and an OpenAI-compatible server answers `GET <base_url>/models`. Each entry
becomes `<provider>/<id>`, with OpenRouter's `context_length` as its window and
`free` set when its prompt and completion cost "0" and it takes tools. The
session's own model is added if neither list has it, so `/model` can always
switch back, and `app.py`'s configure validation, which refuses a model the
catalog lacks, accepts every model of the session's provider. A failed
`/models` costs only that list. `compact` and `review` join the skills as
commands with source `codex`. A probe starts an app-server, asks, and closes
it, about half a second.

The session takes `context_window` from the `modelContextWindow` of each usage
update, since `model/list` carries no window.

### MCP and the primer

`codex/config.py` builds the `-c` flags from `launch.mcp` and the permission
table, with no I/O, as `opencode/config.py`'s `child_config` does for OpenCode.
The `Codex` harness sets `tool_prefix = "mcp__aegis__"`, the name Codex shows,
so the primer (`mcp.py`) names the tools as it does for Claude. The rest of the
primer is the same.

### Titles

The first prompt gives the session its default title, as today. Codex sent no
title in the probes; if a later version sends `thread/name/updated`, the parser
already maps it to `Title`, under the `title_set` rule OpenCode introduced.

### Usage

Usage has three parts in aegis: the card's context and cost, the Usage panel's
quota gauges, and the `aegis usage` report. Codex reports tokens and plan
windows but no money, so each part needs its own source.

**Context.** `context_tokens` is the `Usage.context` of the last
`thread/tokenUsage/updated` of the session's own thread (child threads count as
steps on their `Task` row, not as context). `context_window` is its
`modelContextWindow`.

**Cost.** Codex puts no price on a turn, so `Result.cost_usd` is computed from
tokens with a price per model, and is `None` when the model has none. That is
the rule `usage/scan.py` already follows: a model without a price is unpriced,
never charged at another's rate. The parser prices each request with the model
of its turn's `aegis/turn` line, through `usage/prices.py`'s
`codex_prices_for`:

- `openai/*` models get OpenAI's published API prices, added next to Claude's
  and copied from OpenAI's pricing page when the code is written. For a
  ChatGPT-plan login this is the notional cost of the same work on the API,
  which is what the Claude card shows for a Claude subscription.
- Any model whose id ends in `:free` (OpenRouter's suffix) costs $0.

Everything else (paid OpenRouter models, Ollama, LM Studio) is unpriced, and
the card shows no cost. Pricing paid OpenRouter models would put the catalog's
network prices into the fold, which must give the same answer on every reload;
it can come later as prices written into the `aegis/turn` line.

**Quota.** `quota/codex.py` adds a `QuotaProvider` named `codex`, labelled
"ChatGPT", with `harness="codex"`. Its windows are the response's `primary` and
`secondary`, with kinds named from `windowDurationMins`: `300` is `5h` and
`10080` is `week`, whose spans the provider declares as the existing ones do.
A window of another length keeps its minutes as its kind (`60m`) and gets a
level-only colour, as an unknown vendor window does today.

- `read_token` reads `$CODEX_HOME/auth.json` and returns the ChatGPT account id
  when the login is ChatGPT, and `None` for an API key or no login, so the
  gauge appears only for a plan that has windows.
- `fetch` starts `codex app-server`, sends `initialize` and
  `account/rateLimits/read`, and closes it: about half a second and no tokens,
  through the vendor's own protocol instead of an undocumented endpoint. Its
  floor is 60 s, the existing default.

`Quota.turn_ended` today nudges Claude only. It takes the harness of the
session whose turn ended and nudges the provider with that `harness`, so a
Codex turn refreshes the ChatGPT gauge within its 10 s turn floor. Live
sessions also receive `account/rateLimits/updated` after every request, which
could update the gauge with no fetch at all; the poll plus the nudge is enough
for now, and the push can come later without a protocol change.

**The report.** `usage/scan.py` and `usage/store.py` read `src: "codex"` lines.
Each `thread/tokenUsage/updated` is one model request, counted once by
`codex:<thread>:<turn>:<n>` with its `last` breakdown, priced through the same
table as the card.

## Changes outside `src/aegis/codex/`

- `harness.py`: `harness_for` gains a `codex_bin` argument and the `codex`
  branch. `session.py`, `registry.py` and `app.py` carry a `codex_bin` beside
  their `opencode_bin`, `config_ops.py` and `cli.py`'s `_bins` add `codex` to
  their maps, and `app.py`'s `_bin` picks it. A mapping in place of one
  argument per harness would be tidier, and is a refactor for its own PR.
- `transcript/entries.py`: `PARSERS["codex"]`.
- `cli.py`: a `--codex` option beside `--claude` and `--opencode` on `serve`,
  `doctor` and `init`, carried through the detached re-exec.
- `agents.py`: `codex` joins `HARNESSES`, with the `provider/model` rule.
- `doctor.py`: the `Codex` label; finding the binary, its version and its
  catalog then works as for the other two.
- `quota/__init__.py` and `registry.py`: the Codex provider joins `PROVIDERS`,
  and `turn_ended` passes the harness.
- `usage/scan.py`, `usage/store.py`, `usage/prices.py`: the `codex` source and
  its prices.

## Testing

- **`tests/fake_codex.py`**, a stand-in for `codex app-server` speaking JSON-RPC
  on stdio in the recorded shapes, deltas included. Its scripts are skills,
  since aegis sends a `/name` line as a skill: `/sleep N`, `/stream N`,
  `/fail`, `/bash D => OUT`, `/patch P`, `/mcp T JSON` (which calls the real
  `/mcp` with the token from `AEGIS_SESSION_TOKEN`), `/spawn` (a child
  thread), `/big N` (a line over 64 KB), `/ask` (a request from the agent),
  `/recall`, `/argv`, `/env`, `/body`, `/exit N`. `turn/steer` adds a user
  message at the next step and `turn/interrupt` ends the script with status
  `interrupted`. Opening a thread starts a `sleep` grandchild that holds a
  lease file, so a fake that dies without closing it blocks the next resume
  unless its process group was killed: the group kill is tested.
- **Recorded fixtures** under `tests/fixtures/codex/` from real 0.162.1 runs: a
  plain turn, a tool turn, an MCP call, a steer, an interrupt, a subagent, a
  compaction, and a ChatGPT `account/rateLimits/read` answer.
  `tests/test_codex_stream.py` folds each and checks the entries.
- **`tests/test_session_codex.py`** runs the session scenarios against the fake:
  send, steer, inbox delivery, interrupt, resume after a restart, a lost resume,
  a provider change, a permission change with no restart, commands, exit. Each
  ends with the check that the patches add up to a fresh fold.
- **`tests/test_quota_codex.py`**: window kinds from `windowDurationMins`, a
  window of an unknown length, and no gauge for an API-key login, on a fixture
  recorded from a real ChatGPT login.
- **Browser:** spawn the Codex agent from the new tab against the fake, and see
  text drawn before the turn ends.
- **`make test-live`** gains a Codex session on an OpenRouter free model,
  skipped when `codex` is not installed or `OPENROUTER_API_KEY` is unset. The
  test points `CODEX_HOME` at a temporary directory whose `config.toml`
  defines the OpenRouter provider, so it does not depend on the person's Codex
  config. It spawns, calls the aegis `meta` tool, interrupts a `sleep`,
  resumes after a stop, and checks that a `read` session's write fails. It
  costs nothing; a 429 from the free pool skips it.
- **`make bench`** gains a scenario that replays a Codex transcript, deltas
  included.

## Out of scope

- The approval card, for all three harnesses (the OpenCode spec's issue).
- Codex's cloud tasks, realtime voice, goals, thread forking, and attaching
  Codex's own TUI to a running thread.
- Listing the models of non-OpenAI providers from their `/models` endpoints.
  The catalog has what `model/list` and the agents name.
- An OpenRouter spend gauge. `GET /api/v1/key` reports daily, weekly and
  monthly spend, but no window or limit unless one is set, so it fits a spend
  line better than a gauge.

## Risks

- **The protocol moves.** app-server is marked experimental and Codex ships
  often. The live test is the canary, `Init` records the version in every
  transcript, the store keeps raw lines so a parser fix applies to old
  transcripts, and doctor reports the installed version.
- **The quota path is unverified.** The probes ran without an OpenAI account,
  so the shape of a ChatGPT `account/rateLimits/read` answer is from the schema,
  not from a real response. Recording that fixture needs one `codex login`
  (a free ChatGPT account works) before the quota code is written.
- **The writer lease after a crash.** The group kill after every exit covers
  a Codex crash and aegis's own shutdown. If aegis itself is SIGKILLed, nothing
  runs it; a resume that then meets "active writer" is reported as an error
  with that text, so the person can see why.
- **Free models are rate-limited upstream.** The probe met a 429 on
  `google/gemma-4-31b-it:free`. That only affects the live test and free-model
  agents, never the fake.
