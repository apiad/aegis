# aegis — design

How aegis is built, and the rules that hold across its modules. AGENTS.md says what
aegis is and what done means; `know-how/` says how to do a particular job.

This file changes when the architecture changes, and not otherwise. Adding a tool,
a command, a driver or a sidebar section touches none of it. A rule that belongs to
one module lives in that module's docstring, next to the code it protects, so the
person editing the module reads it. The user-facing reference is the mkdocs site
under `docs/`, and the reasoning behind each feature is in `docs/superpowers/specs/`.

## The process model

**`aegis` boots no brain.** A detached `aegis serve` holds the sessions, the queues
and every view; a terminal connects over a unix socket and pipes bytes. Sessions
outlive the terminal, several terminals can watch one daemon, and `Ctrl+Q` detaches
rather than shutting anything down. The daemon keeps whatever code it booted with,
which is the first thing to suspect when an edit does not appear.

**Two co-equal front ends over one backend.** The Textual TUI and the web/PWA client
render the same transcripts with the same fidelity, and both call the same seams:
`SessionManager` for sessions and `commands.dispatch()` for slash commands. A
feature that exists in one front end and not the other is unfinished.

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

**One table per fact that crosses the wire.** Tool glyphs are resolved server-side
and sent to the web client, so the browser keeps no second copy to drift.

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
