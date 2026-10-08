# OpenCode sessions in aegis 2

> **Status:** implemented, 2026-10-08. Plan: `docs/superpowers/plans/2026-10-08-aegis-2-opencode-harness.md`.
> Issue: [#180](https://github.com/apiad/aegis/issues/180).
> Related: [#78](https://github.com/apiad/aegis/issues/78) (ACP sessions never
> streamed), [#49](https://github.com/apiad/aegis/issues/49) (no titles outside
> Claude). Earlier work: the TUI-era ACP driver in `legacy/aegis/drivers/acp.py`
> and `opencode.py`, and its spec `2026-05-20-aegis-acp-drivers-design.md` in the
> Workspace vault.

An agent whose harness is `opencode` spawns from the new tab and behaves like a
Claude session. Its transcript streams token by token, with the same glyphs and
tool rows. It takes prompts, interrupts, slash commands and inbox messages, and
it resumes after a server restart. Its model and effort change while it runs.
It calls every aegis tool over MCP and can be a queue worker. Its card shows
cost, context and a title that OpenCode generates.

The work has two parts. `session.py` talks to `claude` directly today, so the
first part puts a harness interface between the session and its process, with
Claude Code as the first implementation. The second part is the OpenCode
implementation, which drives `opencode serve` over HTTP.

## What OpenCode does

Measured on OpenCode 1.18.31 on zion. The probes and raw events are in the
Workspace at `.playground/opencode-probe/`, and #180 lists the numbers.

| Question | Answer |
|---|---|
| Which interface runs a turn | The v1 HTTP routes of `opencode serve`: `POST /session`, `POST /session/{id}/prompt_async`, `GET /event` (server-sent events), `POST /session/{id}/abort`, `POST /session/{id}/command`. The v2 `/api/session/*` routes admit a prompt and never run it. |
| What a turn emits | `message.updated` per message (role, model, cost, tokens, `time.completed`, `error`), `message.part.updated` per part (`text`, `reasoning`, `tool`, `step-start`, `step-finish`), `message.part.delta` per token chunk of a text or reasoning part, `session.status` busy and idle, `session.error`, and `session.idle` last. One turn with a `bash` call emitted 137 events, 46 of them deltas. |
| A text part | One `message.part.updated` with empty text when it opens, deltas while it streams, one `message.part.updated` with the full text when it closes. |
| A tool part | `pending` with no input, then `running` with input, then `completed` with `output` (or `error`), all under one `callID`. |
| Cost and context | `step-finish` and the closing `message.updated` carry `cost` in dollars and `tokens` (`input`, `output`, `reasoning`, `cache.read`, `cache.write`, `total`). |
| A prompt sent mid-turn | Stored at once as a user message and read at the next step boundary. One assistant message answered two prompts. |
| Abort | `session.error` with `MessageAbortedError`, then `session.idle`, 0.06 s after the call. A running `bash` part completes with "User aborted the command". |
| A slash command | `POST /session/{id}/command` blocks until the turn ends. The user message it creates holds the expanded template and no marker that a command made it. |
| The system prompt | `system` on `prompt_async` applies to that message only. The model followed it and kept its tools. |
| MCP | `OPENCODE_CONFIG_CONTENT` (inline JSON merged over the user's config) with `{"type": "remote", "url": …, "headers": {"X-Aegis-Session": token}}` connected. The model called the tool as `aegis_whoami`, and the server received the header. |
| Permissions | The `permission` config takes `"*"`, the built-in keys (`edit`, `bash`, `external_directory`, `question`, `doom_loop`, …) and any tool name or pattern, such as `"aegis_*"`. |
| Models | `/config/providers` lists each provider's models with `limit.context` and reasoning `variants`: 30 `opencode-go` models, `deepseek-v4-pro` with `high` and `max`, `gpt-5.6-luna` with `none` through `max`, six with none. |
| Commands | `/command` lists 55 entries in the Workspace, each with a `source`: `command`, `skill` or `mcp`. |
| Cost of a process | 310 MB resident when idle, about 2.5 s from start to answering `/global/health`. A `claude` process on the same machine was 311 to 343 MB. |
| Latency | One turn waited 220 s for its first step and the next turn on the same model started after 10 s, so the wait was the provider's. |

## Choices

**`opencode serve` over HTTP, not ACP and not `opencode run`.** `opencode acp`
(measured on the same version, `.playground/opencode-probe/acp_probe.py`) streams
text and reasoning chunks, reports tool calls with their input and output,
lists commands, takes per-session MCP servers with headers, cancels in 0.11 s,
loads and resumes sessions, and switches the model with `session/set_model`. It
exposes no reasoning variant, so aegis could not set an OpenCode session's
effort. It sends no title. It reports cost and context once per turn, with no
per-step numbers, no context limit per model and no child sessions. Text
arrives only as chunks, so the store would have to keep every chunk. ACP is the
way to add a harness that speaks nothing richer, such as lovelaice, and a
generic ACP harness can sit behind the same interface later. `opencode run --format json` is
one process per prompt: a prompt cannot reach a running turn, an interrupt has to
kill the process, and every turn pays the 2.5 s start. The HTTP server has an
endpoint behind every feature above. The vision spec's non-goal "every signal
comes from a structured protocol (stream-json or ACP)" is widened to include
OpenCode's event stream, which is as structured as either.

**One `opencode serve` per session.** One server could host every OpenCode
session, but its MCP config, and with it the `X-Aegis-Session` token that tells
aegis who is calling, is per process. A process per session keeps the token
rule ("no tool takes the caller's handle") intact. It also keeps the process
model the same as Claude's: a child per live session, ended by Stop, Close and
shutdown. A process costs what a `claude` process costs.

**The v1 routes, pinned by a live test.** OpenCode changes its API between minor
versions, and the v2 routes are unfinished. aegis uses the routes the OpenCode
TUI itself uses, and `make test-live` drives a real `opencode serve`, so a
breaking upgrade fails a named test instead of a session.

## Design

### A harness behind the session

`Session` keeps its store, its fold, its status, its inbox and its card. What it
asks of a process moves behind one interface in `src/aegis/harness.py`:

```python
class Process(Protocol):
    pid: int | None
    running: bool
    async def start(self) -> None: ...
    async def send(self, text: str) -> None: ...   # a "/name args" line is a command
    async def interrupt(self) -> None: ...
    async def set(self, kind: str, value: str) -> None: ...
    async def catalog(self) -> Catalog: ...
    async def terminate(self) -> None: ...

class Harness(Protocol):
    name: str          # "claude-code", "opencode"
    src: str           # the store's src tag: "claude", "opencode"
    label: str         # "Claude Code", "OpenCode", for system entries
    tool_prefix: str   # "mcp__aegis__", "aegis_", for the primer
    def process(self, spec: SpawnSpec, launch: Launch) -> Process: ...
    def parser(self) -> Parser: ...
    async def probe(self, cwd: Path, stderr_path: Path) -> Catalog: ...
```

`Launch` carries what the registry decides and the harness only uses: the resume
id, the MCP URL and token, the system prompt, the stderr path, and the
`on_line` and `on_exit` callbacks. `Parser.feed(line) -> list[Event]` turns one
stored line into events. Claude's parser is the existing stateless `parse`;
OpenCode's keeps state across lines, because a part's events need the role of
the message it belongs to.

`set` changes one of `model`, `effort` and `permission` and raises
`ControlError` when the harness refuses. `Session.configure` still decides when a
change applies (`""` now, `"next_turn"`, `"on_resume"`) and records it. `HARNESSES` in `src/aegis/harness.py`
maps a name to its `Harness`, and `agents.SUPPORTED_HARNESSES` becomes its keys.

The Claude implementation is a move: `ClaudeProcess`, `build_argv` and the
control requests stay in `src/aegis/claude/`, behind a `ClaudeCode` harness that
does what `Session.ensure_running`, `send`, `interrupt` and `configure` do
today. The Claude tests pass unchanged before any OpenCode code lands.

Three names change because they now say something false. The meta's
`claude_session_id` becomes `resume_id`, and a meta that still has the old key
is read as before. The fold's exit line reads "OpenCode exited with code 1" or
"Claude Code exited with code 1", from the harness label. `Init` gains the
harness label, so the first system entry names OpenCode and its version.

### The OpenCode process

`src/aegis/opencode/process.py` starts
`opencode serve --hostname 127.0.0.1 --port 0` in the session's cwd and reads
the port from the line it prints (`opencode server listening on
http://127.0.0.1:<port>`). The environment carries:

- `OPENCODE_CONFIG_CONTENT`: the aegis MCP server with the session's token, and
  the permission rules below;
- `OPENCODE_SERVER_PASSWORD`: 128 random bits for this process, sent as basic
  auth on every request, because any local process or page can reach a
  localhost port.

Start waits for `/global/health` (15 s deadline), then subscribes to `/event`
and waits for `server.connected`, so no event of the first turn can be missed.
Then it creates the OpenCode session (`POST /session`), or, on resume, checks
that `GET /session/{resume_id}` exists. Every request passes the cwd as
`?directory=`. The process requests run on an `httpx.AsyncClient`, declared as
a direct dependency (it already comes with fastmcp). Each request is wrapped in
`asyncio.wait_for`, because httpx timeouts count per operation and not for the
whole call.

The event stream is read line by line on a task, and each `data:` line goes to
`on_line` as it arrives. When the child exits, `on_exit` gets its code and
stderr tail, as `ClaudeProcess` reports it. When the stream closes while the
child still runs, the process is terminated and reported as an exit, with
"the event stream closed" in the tail, because a session that can no longer
hear its harness must not look alive.

If OpenCode no longer has the session a resume names (its own database was
cleared), the process creates a new one and the session records a `reset`
record. The transcript shows "OpenCode no longer had this conversation; it
started a new one", the same way Claude's `/clear` is shown.

### What is stored, and the one thing that is not

The store keeps OpenCode's event lines as `{"src": "opencode", "line": …}`,
the same way it keeps `claude`'s stdout. It keeps the event types the fold
reads: `session.created`, `session.updated`, `session.status`,
`session.idle`, `session.error`, `session.compacted`, `message.updated` and
`message.part.updated`, for this session and for the child sessions its `task`
calls start. It drops the rest (heartbeats, `plugin.added`, `catalog.updated`,
`session.diff`, events of unrelated sessions), which are 40 to 50 percent of
the stream and carry nothing the transcript shows.

`message.part.delta` is not stored. A delta is a few characters inside a
350-byte line, so a 2,000-token answer would add about 700 KB to the store,
and the closing `message.part.updated` that follows it carries the same text
whole. A delta is folded live: the session applies it to the fold and publishes
the patch without a store record, so the browser draws text as it arrives.

This is the one place where the live view may be ahead of a fresh fold of the
store. It holds only while a part is open; when the part closes, its stored
update replaces the text and the two agree again. The rule "the patches add up
to the entries" therefore holds at every stored record, and the session tests
check it at the end of every OpenCode scenario, as they do for Claude.

### From events to entries

`src/aegis/opencode/stream.py` maps events onto the event types the fold
already handles, so the entry rules, glyphs and summaries stay in one place.

| OpenCode event | aegis event |
|---|---|
| `session.created` or `session.updated` of this session, the first time | `Init(session_id, model="provider/model", version, harness="OpenCode")` |
| `session.updated` whose `title` is no longer OpenCode's placeholder (`New session - …`) | `Title(text)` |
| `message.part.updated`, `text` part of a user message, not `synthetic` | `Echo(text)` |
| `message.part.updated`, `text` part of an assistant message | `Text(text, key=part id)` |
| `message.part.updated`, `reasoning` part | `Thinking(text, key=part id)` |
| `message.part.delta` | `Delta(key=part id, text)`, live only |
| `tool` part becomes `running` | `ToolCall(id=callID, name, input)` |
| `tool` part becomes `completed` or `error` | `ToolOutput(id=callID, text, is_error)` |
| `step-finish` part | its `Usage`, for the context gauge |
| `session.compacted` | `Compact` |
| `session.error` | remembered for the `Result` that follows |
| `session.idle` | `Result(is_error, subtype, duration_ms, cost_usd)` |

`Text` and `Thinking` gain an optional `key`. With a key, the entry id is the
key, so the opening update, the deltas and the closing update all touch one
entry. Without one, as for Claude, the id stays `e<record>.<block>`.

`Result.cost_usd` is the session's running total: the sum of the latest `cost`
of each assistant message. The fold already turns a running total into the
cost of one turn. `duration_ms` runs from the first `busy` status of the turn.
`is_error` is set when a `session.error` came before the idle event, and its
`subtype` is the error's name (`MessageAbortedError` shows as "interrupted",
through the fold's existing interrupt flag).

A `Usage` comes from `tokens`: `input`, `cache.write`, `cache.read`, and
`output` plus `reasoning`, so `Usage.context` equals OpenCode's `total`.

A `task` call starts a child session. Events of a child session reach the fold
as events whose `parent` is the `callID` of that `task` call, and the fold
counts them as steps on the call, as it does for Claude's subagents.

**Tool names and inputs are translated in the parser**, so `describe.py` keeps
one vocabulary. `bash`, `read`, `write`, `edit`, `glob`, `grep`, `list`,
`webfetch`, `websearch`, `todowrite` and `task` become `Bash`, `Read`, `Write`,
`Edit`, `Glob`, `Grep`, `LS`, `WebFetch`, `WebSearch`, `TodoWrite` and `Task`.
Their camelCase inputs (`filePath`, `oldString`, `newString`) become the snake
case keys `describe.py` reads, which also gives OpenCode's edits the diff
window. `aegis_<op>` becomes `mcp__aegis__<op>`, and other MCP tools keep
their names. A name the table does not know passes through unchanged and gets
the generic tool glyph. The stored line keeps OpenCode's own names.

**A command's echo shows the line as typed.** OpenCode echoes the expanded
template, which matches no pending send. When an `Echo` matches no pending text
and a pending `/` line is waiting, the fold takes that line and shows it as the
user entry, with the expanded template collapsed under it. This is a fold rule,
so it applies to any harness that echoes a command's expansion.

### Sending, commands and interrupting

`send` is `POST /session/{id}/prompt_async` with the text, the model
(`providerID` and `modelID` split from the spec's `provider/model`), the
variant, and `system` set to the aegis primer plus the agent's priming. The
system prompt goes on every prompt, because OpenCode applies it per message.
The session moves to `working` on send, as it does for Claude.

A prompt sent mid-turn goes straight to OpenCode, which reads it at the next
step boundary, as Claude does at a tool boundary. The inbox keeps holding its
messages until the turn ends; nothing about it changes.

`/name args`, where `name` is in the session's OpenCode catalog, goes to
`POST /session/{id}/command` with the same model and variant. That call blocks
until the turn ends, so it runs on its own task, and a failure is recorded as
an error entry. The next send waits until OpenCode has echoed the command, or
a prompt could overtake it. A command carries no `system`: the endpoint takes
none. aegis's own commands (`/model`, `/effort`, `/permission`,
`/rename`, `/title`, `/stop`, `/close`) are resolved before this, as today.
`//rest` is sent as the prompt ` /rest`.

`interrupt` is `POST /session/{id}/abort`. The turn ends through the
`session.error` and `session.idle` that follow, and the existing deadline marks
the session `error` if they never come.

### Model, effort and permission

OpenCode takes the model and the variant with each prompt, so `set` changes
`model` and `effort` without a request: the next prompt carries them. The
registry validates both against the catalog first, as it does for Claude: the
model must be listed, and the effort must be one of that model's variants. A
model with no variants takes no effort, so a spec's effort is not sent for it.

The permission lives in the child's config, so `set` keeps the new rules. The
next send after the turn has ended first restarts the child with the new
config and resumes the same OpenCode session. The process counts itself busy
from a send until OpenCode reports idle, so a prompt sent mid-turn never
restarts it. The restart stays inside the OpenCode process object, so
`Session` does not know it happened.

aegis's four permissions map to rules in which nothing asks, because aegis has
no approval prompt (see Out of scope):

| aegis | OpenCode rules |
|---|---|
| `read` | `"*": allow`, `edit: deny`, `bash: deny`, `task: deny`, `external_directory: deny` |
| `write` | `"*": allow`, `bash: deny`, `external_directory: deny` |
| `auto` | `"*": allow`, `external_directory: deny` |
| `full` | `"*": allow` |

Every mode adds `aegis_*: allow`, `question: deny` and `doom_loop: deny`. The
`question` tool asks the user and waits for a reply nobody can send, and
`doom_loop` asks before letting a repeated identical call through. A unit test
fails if any mapped value is `ask`. The order `read < write < auto < full`
stays strict, so an agent spawning a session still cannot gain power. The live
test checks that a `read` session's edit is refused.

### The catalog

`OpenCode.probe` and a live process's `catalog` read `/command` and
`/config/providers`. A command keeps its name, its description and its
`source`. A model is `provider/model`, labelled with its name, with its
variants as efforts and its `limit.context` as a new `Model.window`. The
catalogs in `commands.Catalogs` are keyed by harness and cwd, since a Claude
and an OpenCode session in one cwd have different commands. A probe starts an
`opencode serve`, asks, and ends it, about 3 s.

The session takes `context_window` from its model's `window` when the catalog
arrives and when the model changes, since OpenCode reports no window in its
events. The model chip on the spawn form offers the models the agents name (and
Claude's aliases for Claude); the `/model` menu in a session offers the catalog's.

### MCP and the primer

`mcp.py` gains `opencode_config(url, token, permission)`, the
`OPENCODE_CONFIG_CONTENT` JSON. The primer names the tools by the harness's
prefix: `mcp__aegis__*` for Claude, `aegis_*` for OpenCode. The rest of the
primer is the same.

### Titles

The first prompt gives every session its default title, as today. When OpenCode
generates a title (`Title`), the session takes it, unless a person or an agent
has set one through `session.rename`. The meta records `title_set` for that,
and `registry.rename` sets it.

### The card and the quota

The card shows `cost_usd` (the running total from the last `Result`),
`context_tokens` (from the last `step-finish` usage of this session),
`context_window` (from the catalog) and `model_id` (from `Init`). While a turn
has no step yet, its activity reads "waiting for the model". The OpenCode
Go quota gauge (`quota/opencode.py`) is already wired and does not change.

## Testing

- **`tests/fake_opencode.py`**, a stand-in for `opencode serve`, like
  `fake_claude.py`. It serves the routes above on the port it prints, checks the
  basic auth, emits events in OpenCode's shapes (taken from the recorded
  fixtures, deltas included), and runs a script picked by the prompt's text:
  `/sleep N`, `/fail`, `/bash D => OUT`, `/mcp T JSON` (which calls the real
  `/mcp` with the token from `OPENCODE_CONFIG_CONTENT`), `/task`, `/exit N`,
  `/recall`, and text that quotes the prompt. Prompts sent mid-turn are read at
  the next step boundary, abort ends a script with `MessageAbortedError`, and
  `/command` echoes the expanded template.
- **Recorded fixtures** under `tests/fixtures/opencode/`, from real 1.18.31
  runs: a plain turn, a tool turn, a mid-turn prompt, an abort, a command, a
  `task` with a child session. `tests/test_opencode_stream.py` folds each and
  checks the entries.
- **`tests/test_session.py`** runs the scenarios that apply to both harnesses
  with each of them: send, mid-turn send, interrupt, resume after a restart,
  inbox delivery, configure, stop, exit. Every one ends with the check that the
  patches add up to a fresh fold.
- **Browser:** spawn the OpenCode agent from the new tab against the fake, and
  see text drawn before the turn ends.
- **`make test-live`** gains an OpenCode session on `opencode-go/deepseek-v4-flash`,
  skipped when `opencode` is not installed: spawn, call `aegis_meta`, interrupt a
  `sleep`, resume after a server restart, and a `read` session refused an edit.
  A run costs about a cent.
- **`make bench`** gains a scenario that replays an OpenCode transcript through
  the fake, deltas included.

## Out of scope

- An approval card for permission requests and questions, for Claude and
  OpenCode both. It gets its own issue.
- Forking and sharing OpenCode sessions, and attaching OpenCode's own TUI.
- Importing the legacy tree's OpenCode sessions.
- lovelaice, and other harnesses (#59, #105).
- OpenCode's v2 `/api/session` routes, until they run a turn.

## Risks

- **OpenCode's API moves.** The live test is the canary, `Init` records the
  version in every transcript, and the store keeps raw lines, so a parser fix
  also applies to old transcripts.
- **A provider stall looks like a hung session.** The 220 s wait above showed as
  `busy` with no parts. The card's "waiting for the model" makes a stall
  visible, and Interrupt still ends it.
