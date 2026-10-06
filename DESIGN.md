# aegis — design

How aegis is built, and the rules that hold across its modules. AGENTS.md says what
aegis is and what done means; `know-how/` says how to do a particular job.

This file changes when the architecture changes, and not otherwise. Adding a tool,
a command, a driver or a sidebar section touches none of it. A rule that belongs to
one module lives in that module's docstring, next to the code it protects, so the
person editing the module reads it. The user-facing reference is the mkdocs site
under `docs/`, and the reasoning behind each feature is in `docs/superpowers/specs/`.

The repo holds two trees. aegis2, under src/aegis2/, is where new work goes, and
its design comes first. The old tree under src/aegis/ is frozen and takes bug fixes
only. Its design follows unchanged, because those fixes still have to respect it.
Each aegis2 rule below lands with the slice that first needs it, and every slice PR
updates this part in the same change that alters the shape.

# aegis2

## The process model

**One process per machine, one websocket per browser.** `aegis2 serve` serves the
static client, holds the sessions and runs each harness as its own child process.
A browser talks to it over one websocket. Closing the browser leaves the sessions
running; stopping the server ends their processes, not the sessions. Later slices
add the home server, links to other servers and plugin hosts, in the order the
vision spec gives.

**A session outlives its process.** A session is live (a `claude` child is
running), stopped (none is), or archived (closed: no process, no tab). A prompt to
a stopped session starts `claude --resume`, which prints nothing old, so the store
and the fold simply continue. Only shutdown, Stop and Close end a process. Nothing
reaps idle sessions, because a Claude session waiting on its own background task
wakes itself when the task ends, and stopping it would kill the task.

**Boot reads meta files, never stores.** Each session has a small JSON meta next to
its store, written by write-then-rename. Boot reads only those, so an archive of
hundreds costs hundreds of small reads; a missing or damaged meta is rebuilt from
its store and the session lands in the archive. Boot writes to no store, except one
`server_stopped` record for a session whose meta says it was mid-turn.

**Separate state, separate port.** aegis2 keeps its state in `.aegis2/state/`
under the config root and listens on its own port, so it runs next to the old tree
on one machine. It never reads the old tree's state. The one file both read is
`.aegis.yaml`, and aegis2 reads only its `agents:` map.

**No imports from the old tree.** Code that is already right is copied and
adapted, never imported, and imports inside aegis2 are relative.
`tests/aegis2/test_imports.py` enforces both by AST. The second makes the final rename one mechanical commit.

## Rules that span modules

**The tabs are the server's; their order is the browser's.** The tab bar is the
server's open sessions, the same in every browser, so Close on any browser removes
the tab everywhere and nothing accumulates in a browser. Which tab is focused and
the order of the tabs belong to each browser (the URL hash and local storage); the
server never sees them.

**A session's card is published when it changes, at most four times a second.**
Status, handle and title go out at once; activity, context and cost change on
nearly every line of a turn and are coalesced for 250 ms. Publishing them per line
doubled the server's cost per line.

**One registry, every caller.** Every action is one registered operation with a
pydantic params model. A websocket `call` is one projection of the registry, MCP
tools are another, and plugins add operations to it. Nothing reaches the client as
an action that is not an operation, so a subsystem cannot exist for agents and be
missing from the UI. The retired September client died of the opposite: a protocol
in which every subsystem needed its own fields.

**Agents call the same operations, as MCP tools named after them.** An operation
marked for agents is served at `/mcp` under its name with the dot as an underscore,
so `monitor.start` is `mcp__aegis__monitor_start`. The caller is the session whose
token the request carries; the token is minted for each `claude` process and rides
in its `--mcp-config` header. No tool takes the caller's handle as an argument,
because an argument can be wrong and the token cannot.

**The inbox holds messages until a turn ends.** A monitor's wake, a queue's result
and a handoff reach a session as a user turn headed `> from <kind>:<name> · …`. An
idle session gets it at once and a stopped one is resumed for it; a working one
holds it and gets every held message as one turn when its turn ends. Writing to a
working `claude` would inject the message at its next tool boundary instead.

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
queue worker is finished only when its turn has ended with no live monitor, no
held message and no Claude task still open (`task_started` without its
`task_notification`). Reading the turn boundary alone as done closed a worker
mid-wait in the old tree.

**Agents change only what they created.** An agent reads and messages any session
it can see, and changes only its own monitors, its own session's names and the
tasks it enqueued. People can do anything.

**The client knows no subsystem by name.** Server state reaches the browser as
named channels: a snapshot on subscribe, then numbered patches. A gap in the
numbers, or a reconnect, means resubscribe and take a fresh snapshot. Adding a
subsystem adds operations and channels, never a protocol field.

**Python decides, the browser draws.** Every fact and decision about a transcript
entry is computed once in Python: its glyph, title, summary, status, the diff
window of an edit, and what collapses. The entry crosses the wire as data and the
browser only turns it into markup. One copy of each fact means no drift, and a
data protocol version fails loudly across a link where mismatched markup would
break silently.

**A renderer is a function that returns a Node.** The client is plain ES modules
with no framework and no build step. A plugin's renderer has the same shape, so
writing one needs no framework.

**A theme is one CSS file over one markup.** The markup carries everything any
theme might show, and the base stylesheet reads only CSS variables. A theme sets
the variables and a few overrides that decide what shows.

**The store keeps raw lines; entries are derived.** A transcript file holds the
harness's raw stdout lines and what aegis2 did (spawn, send, interrupt, exit,
close), each with its receive time and its own index. Entries are folded from them
on load, with deterministic ids, so a better summary applies to old transcripts and
a resume reads the same file the live session wrote.

**The patches add up to the entries.** A browser that saw every patch from a
snapshot on holds exactly the session's live entries, and those equal a fresh fold
of the store. A reload and a live view must never disagree. The session tests check
both after every scenario, because the first bug the browser found was a patch that
left out an update the entries had.

**A transcript is keyed by a log id minted at spawn, never by a handle.** Handles
are reused; keying on them once merged unrelated conversations into one file in
the old tree.

**The echo creates the user entry, never the send.** Claude Code injects a prompt
sent mid-turn at the next tool boundary and closes both prompts with one `result`,
so a turn is not one prompt and nothing counts turns by counting sends. A sent
prompt is pending until Claude echoes it, and the transcript records the order the
model read things in.

**System notices never start a turn.** Hook, init, thinking-token and task notices
arrive both inside and outside turns. Only a sent prompt or a turn-bearing event
moves a session to working, and only `result` or the end of the stream moves it
out. Promoting a notice to a turn parked old-tree sessions on a read that never
returned.

**Three roots, never `Path.cwd()`.** The CLI reads the working directory once to
build the config root, state root and harness cwd, and passes them down. Nothing
below it calls `Path.cwd()`, and `tests/aegis2/test_no_cwd.py` fails if something
does.

**A damaged file never takes a session down.** A store line that does not parse is
skipped and counted; a stdout line that does not parse is stored and shown as a
system entry.

**Agent text is untrusted.** Markdown renders with raw HTML disabled, and the
localhost port is not trusted either: the websocket needs the server's token and
its own origin, because any page in the browser can open a socket to localhost.

**Performance is measured on every PR and never gates.** `scripts/bench2.py`
replays a recorded transcript through the fake harness in
`tests/aegis2/fake_claude.py`, and CI compares the PR's base and head on the same
runner. A regression is a warning a reader has to weigh.

# The frozen tree: aegis

## The process model

**`aegis` boots no brain.** A detached `aegis server` holds the sessions, the queues
and every view; a terminal connects over a unix socket and pipes bytes. Sessions
outlive the terminal, several terminals can watch one daemon, and `Ctrl+Q` detaches
rather than shutting anything down. The daemon keeps whatever code it booted with,
which is the first thing to suspect when an edit does not appear.

**A browser is a view, like a terminal.** `aegis web` is a separate process
that serves each browser tab one view over the daemon's unix socket and
relays its frames unchanged. It checks the token and knows nothing else:
no session, agent or queue crosses into it, and `tests/webterm/test_imports.py`
fails if one does. The daemon binds no web port, so the process facing a
network is never the one running the agents.

**One boot path.** Every entry point goes through `_serve` in `src/aegis/cli.py`,
which takes an `AegisRoots` and an optional UI attachment. With no attachment it
starts the MCP plane itself; with one it defers `start()`, because `build_server`
captures the bridge at `start()` and a front end rebinds the plane to itself first.
`load_boot_config` raises `ConfigError` instead of exiting, so a host that embeds
aegis sees the exception.

**Three roots, never `Path.cwd()`.** `config_root`, `state_root` and `harness_cwd`
coincide for the CLI and differ when aegis is embedded, so every path is resolved
against the right root. They were conflated as `Path.cwd()` for a long time, and
`tests/test_no_cwd_regression.py` guards the regression by AST.

**`.aegis.yaml` is the single config file.** Drop-in overlays under
`.aegis/<kind>/*.yaml` merge with inline entries and fail loud on a collision,
because a silently shadowed agent or queue is a worse failure than a refused boot.

## Rules that span modules

**A view adopts the brain's planes; it never builds its own.** Queues, monitors,
reminders, the inbox, canvas, terminals, groups and claims are brain state. Agents
reach the brain's copy through MCP, so a copy a view built for itself renders
something nothing writes to. `src/aegis/core/planes.py` is the inventory, and a test
forces every new `attach_*` on `SessionManager` into it.

**A view opens sessions on the brain; it never builds one.** Only the brain mints
the token a harness presents to MCP and lists the session in
`aegis_list_sessions`. A session a view built itself is on screen and unknown to
every agent, so `read_peer`, `handoff`, `rename` and `/loop` fail for it. New tab,
`/spawn`, `/fork`, a history reopen, the boot restore and a reconnect all go through
`SessionManager`, and a bridged view that reaches for its own raises.

**One authority for handle names.** A handle bound anywhere in a process is never
handed to a different session. A pane's DOM id is its birth handle and Textual ids
are immutable, so reusing a name is `DuplicateIds` and takes the whole app down.
Mint through `SessionManager.handles`, never `generate_name`.

**A transcript is keyed by a log id minted at spawn, never by a handle.** Handles
come from a finite pool and are reused across restarts; keying on them once merged
unrelated conversations into one file (100 of 223 logs on a real state dir). A
rename appends metadata and moves nothing.

**A damaged file never takes a session down.** Transcript reads skip damaged lines
and salvage what they can, and observability writes (the comms ledger, the process
log) log their own failure instead of raising into the call they observe.

**A turn boundary is not completion.** Ending a turn is how an agent waits on a
monitor or a reminder, so nothing that finalises a worker (marking a task done,
sending a callback, closing it) may read a turn boundary as done. Reading it that
way once closed a worker mid-wait and stranded real work in a shared checkout.

**Replay does not re-record.** Recording hooks (which repos a turn wrote to, what a
turn did) hang off `AgentSession._fire_event`, which the replay walk does not call.
Moving one somewhere replay reaches makes a resumed session re-collect its whole
history as a single turn.

**What aegis says about a turn stays out of the agent's context.** Recaps and side
notes land in the pane's history and are never appended to the session log, which
keeps them out of the model's context and stops summaries compounding into
summaries of themselves.

**Caller identity is a transport fact.** One co-resident MCP server means no
per-connection identity, so aegis mints a token per harness spawn and carries it in
a request header that `resolve_caller` reads back. A tool's `from_handle` argument
is a convention; the token is what attribution trusts.

**Off-host paths are never resolved against the local disk.** The same string names
a different tree on another host, and resolving it locally returns a wrong answer
instead of an error. Claims, the repos board and file targets all carry the host.

**Spawn has three orthogonal axes.** Agent profile, harness and host are resolved
per spawn. Per-session overrides (model, effort, prompt, host) are never persisted
to config.

**One table per fact that crosses the wire.** Tool glyphs are resolved once, in
Python, and reach every frontend already rendered. The retired browser client
kept a second copy and it drifted the first time a glyph was added on one side.

**Extension without forking.** Users extend aegis through three shapes: `@workflow`
(orchestration invoked by a user, an agent or the scheduler), `@hook` (harness
lifecycle events) and `@tool` (an MCP tool an agent can call). Plugins are
auto-imported from `.aegis/plugins/` and the `plugin_dirs` config.

## What a machine checks, and what a reader judges

`make check` runs formatting, lint, doc lint, type checking and the fast test lane.
`make test` is the lane to iterate on. A red run is a regression to investigate, not
noise to re-roll: the suite's old flakes (a leaked inotify instance per app, two
teardown races, tests sharing one `.aegis/state`) were fixed, and re-running a red
test until it passes would now hide real failures.

`.rift.yaml` checks that the docs name only real files and real tools, that the
rosters (slash commands, drivers, config sections, mkdocs pages) are documented, and
that the agent docs follow their contract. It runs from the Makefile only: rift is
private, so CI cannot install it.

A reader still has to judge:

- whether a doc's explanation is correct, not merely that its nouns resolve;
- whether `src/aegis/`-relative shorthands (`tui/pane.py`) still point at real
  modules, since rift resolves paths against the repo root only;
- whether every MCP tool is documented at all, since `docs/mcp.md` is an
  architecture page and not a tool reference;
- anything about `Path.cwd()` discipline beyond what the AST test covers.

## Non-goals

- Log scraping. Every signal comes from a structured protocol (stream-json or ACP),
  never from parsing terminal text.
- A line REPL or `--plain` mode. The TUI requires a TTY.
- Replacing the underlying agent. If an upstream CLI improves, aegis improves with
  it.
