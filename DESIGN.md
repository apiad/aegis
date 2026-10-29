# aegis: design

How aegis is built, and the rules that hold across its modules. AGENTS.md says
what aegis is and what done means; `know-how/` says how to do a particular job.

This file changes when the architecture changes, and not otherwise. A rule that
belongs to one module lives in that module's docstring, next to the code it
protects. The reasoning behind each feature is in `docs/superpowers/specs/`: the
vision (`2026-10-05-aegis-2-vision-design.md`) and one spec per slice. This tree was
built as a second package next to the TUI-era aegis, which now lives under
`legacy/` with its own `legacy/DESIGN.md`.

## The process model

**One process per machine, one websocket per browser.** `aegis serve` serves the
static client, holds the sessions and runs each harness as its own child process.
A browser talks to it over one websocket. Closing the browser leaves the sessions
running; stopping the server ends their processes, not the sessions. The window
`aegis` opens is the user's browser started detached, never a child of the server,
so stopping the server leaves it to reconnect (`window.py`).

**Or inside another program, as a library.** `Server` (`server.py`) builds what
`aegis serve` builds, after the same checks (`prepare()`, which the CLI calls
too), and runs uvicorn as a task in the host's event loop, on a socket bound
first so `base_url` is known, with no signal handlers. It always listens:
harness children reach aegis's tools at `<base_url>/mcp`. One runs per process
at a time, because `launch.configure` sets a mode every child process reads.
`aegis.api` puts typed objects over the registry, never beside it: `Session` is
a handle by log id whose methods are operations called as the admin person,
its models are parsed from the wire's dicts with unknown fields ignored, and
`srv.ops` is every operation as a method, its signatures generated into
`api/ops.pyi` from the params models.
`Task`, `WorkflowRun` and `Schedule` are handles of the same kind. A run is
armed and answered as its owner session, an agent caller, because a run
always has a main agent that aegis wakes; the rest acts as the admin person.

**A server may link others, as their client.** A link (`links.py`) is one
websocket this server opens to another, saying `hello` with that server's token,
exactly as a browser does. The people on this server see and drive the linked
server's sessions through it: a browser message naming another `server` goes
down the link and comes back stamped with it. Links are kept in
`<state>/links.json` (mode 0600, never `.aegis.yaml`, which is in git and read
by both machines), and `aegis link add` or an edit to the file takes effect
while the server runs. Plugin hosts come later, in the order the vision spec
gives.

**A session outlives its process.** A session is live (a harness child, `claude`,
`opencode serve`, `codex app-server` or `cursor-agent acp`, is running), asleep (no process, the
conversation kept), or archived (no process, no tab). An asleep session carries
`asleep_why`: `slept` (Sleep or `session.stop`), `idle` (a park), `exited` (the
harness ended on its own; mid-turn or with a non-zero code it is an error,
between turns with code 0 it is not) or `server` (the server restarted under it). A meta
written before that state says `stopped` or `parked`, and loads through
`session.asleep_from`, so nothing on disk is migrated. Quiet is the other axis:
it hides the tab and never touches the process. A prompt to an asleep session starts the harness again on the same
conversation (`claude --resume`, `opencode serve` and the same session id,
`thread/resume` on the same Codex thread, or `session/load` on the same Cursor
session id),
which prints nothing old, so the store and the fold simply continue. Shutdown, Sleep, Restart (`session.restart`: the
process ends and a fresh one resumes the conversation, under the start lock), Archive and a park end a process.
A park (park.py) happens only when ending the process kills nothing: no background
task, no harness timer, no process it left running, and nobody watching. A Claude
session waiting on its own background task wakes itself when the task ends, so a
park would kill the task; that is why each of those blocks it.

**A session's memory is its own, where the unit delegates.** When the server
runs as a systemd service with `Delegate=yes`, it moves itself into
`<unit>/server` at boot, before any session starts, and runs each harness
child in `<unit>/sessions/<log_id>` with `memory.max` from
`session_memory_max`. The child joins its cgroup before it execs, through a
`/bin/sh` wrapper, so nothing it starts escapes the cap. When the process ends
(exit, Sleep, Restart, Archive, shutdown) its cgroup is killed and removed, and boot
removes the empty ones a crash left. Everything stays inside the unit, so the
unit's `MemoryMax` still covers the whole tree and `KillMode` still kills every
session when the unit stops. Without delegation every child lives in the
server's cgroup, as before (`cgroups.py`, #360).

**In a host OOM, a session dies before the server.** The server raises each
child's `oom_score_adj` to 500 right after the spawn
(`harness.raise_oom_score`), so a runaway tree is killed instead of the server
that holds every session; a deployment gives the server a negative
`OOMScoreAdjust` to widen the gap (#360).

**A terminal is a PTY the server holds, beside the sessions.** A terminal
(`terminals.py`, #362) is a child on a PTY, started from a profile in the
`terminals:` block or from the built-in shell. It belongs to the server and has
an owner, so it outlives every browser. Its reader (`terminal.py`) reads at
most 4 MB/s and flushes what it read every 16 ms. Everything it reads goes to
an asciicast store on disk (`term_store.py`), whose newest 1 MB is also kept in
memory. Its card rides the `sessions` channel with `kind: "term"` and `log_id`
set to its id, because the channel and the client key every card by `log_id`.
Its output rides `term:<id>`: a browser holding a revision that memory still
covers gets the events after it, and any other gets the raw output since the
last screen clear, at most 256 KB. The browser's xterm.js is the only terminal
emulator; the server keeps the bytes and parses none of them, because a Python
parser measured at under 0.4 MB/s cannot keep up with the reader
(`docs/superpowers/specs/2026-10-14-terminals-design.html`).

**A browser is a Chromium the server holds, shared by an agent and a person.**
A browser (`browsers.py`, #564) is a headless Chromium driven through
Playwright's persistent context, so its profile under
`<state>/browsers/profiles/<name>/` keeps sign-ins across stops. Playwright is
the optional `browser` extra and is imported only when a browser starts. Its
card rides the `sessions` channel with `kind: "browser"`, as a terminal's does.
Its frames ride `browser:<id>`: a watcher polls the channel's subscriber count
and runs `Page.startScreencast` only while a tab watches, and acks each frame
no sooner than 100 ms after the last, so a pane gets at most ten a second. The
person's input comes back as CDP input events (`browser.input`), refused while
the agent holds the page; the agent's `browser_*` tools are Playwright calls,
refused while the person holds it. `holder` says which. The client's view of
a browser (`browserview.js`) builds its own DOM, so it mounts in the
browser's tab and, as pane `b-<id>`, in the canvas of the session that drives
it (`Session.dockable`); each view subscribes on its own. A give-back or a give
wakes the driving session through its inbox with the person's events since the
handover. An init script reports clicks, typing (a password field's length
only) and submits through a binding, with the target's role and name; page
listeners add navigations, console errors, failed requests and dialogs. All of
it goes to `<state>/browsers/<id>/events.jsonl`
(`docs/superpowers/specs/2026-10-22-browser-design.html`).

**Boot reads meta files, never stores.** Each session has a small JSON meta next to
its store, written by write-then-rename. Boot reads only those, so an archive of
hundreds costs hundreds of small reads; a missing or damaged meta is rebuilt from
its store and the session lands in the archive. Boot writes to no store, except one
`server_stopped` record for a session whose turn the last run's end cut. Continuing
those turns is not part of reading: `continue_after_boot` runs after it, before the
monitors are armed, and writes through the ordinary send path. A handoff
(#354) is meta too: the new session's spec names the session it `continues`,
the old one's meta names it in `continued_by`, so the old session is archived
when the new one's first turn ends, before or after a restart, with nothing
held in memory.

**A restart gives back what it cut.** aegis writes `<state>/server.json` at both ends
of a clean shutdown, so boot tells a restart from a crash. A turn the server's own
stop cut continues at the next boot, with one message ahead of whatever the session
was holding: up to twice in a row after clean restarts, once after a crash, because
a turn can take the server down and continuing it again would take it down again.
A harness that failed on its own is never restarted. After a crash, a harness the
dead server left running is ended first, recognised by its pid and its start time
in `/proc`, so two processes never resume one session. Under systemd's default
`KillMode=control-group` the harnesses die a moment before aegis does; an exit by
SIGTERM that cut a turn within 30 s of a clean stop counts as that stop's, and a
worker's task waits out those 30 s before such an exit fails it (restarts.py).

**Its own state, never mixed with the legacy tree's.** aegis keeps its state in
`.aegis/state/` under the config root, where the legacy tree kept its own in
another format. `aegis serve` refuses to start on a state directory holding the
legacy tree's marker files, and says to move it to `.aegis/legacy-state/`; mixing
the two would have each read files the other wrote. `aegis import-legacy` crosses
that line once, in one direction: it rewrites each legacy log as the stream-json
lines Claude would have printed, into aegis's own store as an archived session,
so the fold reads it like any other and nothing reads the legacy directory again
(`legacy_import.py`). Of `.aegis.yaml`, aegis reads
the `agents:` and `queues:` maps and `default_agent`, and nothing in them is a
default: an agent names its harness, model, effort and permission, a queue its
agent and `max_parallel`, and one that does not is reported by name rather than
filled in or dropped. A setting the loader filled in would be one nobody chose,
with nothing to show it.

**The file is the configuration.** The server holds a parsed copy and re-reads
it whenever its stamp changes (`config.py`), so an edit made in an editor and a
save from the Settings page take the same path, and nothing patches server state
behind the file's back. A file that does not parse, or is empty, leaves the last
one that did in force and says so, because a half-typed edit must not stop every
spawn. `aegis init` and the Settings page write through one writer that keeps
comments and refuses a stale write; `aegis doctor` and `config.doctor` check the
file against the harnesses actually installed (`doctor.py`). The Settings form
edits every key the readers take: the blocks that are values over defaults are
drawn from `fields()`, a schema the wire carries, and the rest have controls of
their own. `tests/test_config_coverage.py` takes the keys from the readers'
declarations, so a key added to a reader fails until the form's document and
the page carry it. Its `context:`
block is the one section with defaults: a wrong one keeps them all in force
and `aegis doctor` names the mistake, because a typo there must never stop a
`turn_end`.

**The systemd unit comes from one template.** `aegis service install` renders
it, the README's example is it rendered for a host with 8 GB, and `aegis
doctor` compares an installed unit against it rendered for this host
(`service.py`). Hand-written units drifted from the recommended settings and
nothing said so; one template and a test against the README keep the three
from disagreeing. aegis writes the unit where the person says and prints the
`sudo` commands, because installing a system service is theirs to run.

**The code map is a subcommand, with its index in the user's cache.** `aegis
map` parses a git repository with tree-sitter and prints its layout, outlines,
callers and impact. The code lives in its own package, [wayfind](https://github.com/apiad/wayfind), so it can run where aegis does not, such as CI; aegis depends on it and mounts its typer app as `aegis map`. It is a CLI rather than MCP tools because a
tool's schema rides on every request of every session, and a subcommand costs
nothing until it runs; the primer names it in one paragraph. Its index is one
pickle per repository under the user's cache directory, refreshed by mtime and
replaced atomically, so every session of the user shares it without the server
in the way. It needs no language server: heuristics marked as such (`?` for a
name-only edge) were right often enough on a measured sample, and work in every
language the grammar pack has. What one language adds over that, receiver
types, base classes and an exact branch count, is one table of query
patterns per language (wayfind's `specs.py`), so a language is data, not code.
Its numbers views (metrics, cycles, unused, diff) report and never judge: a
threshold is the caller's, so a CI gate is a filter over `--json` rather than
a script beside the map.

**Relative imports, and nothing from the legacy tree.** Imports inside aegis are
relative, which is what made taking over the `aegis` name one directory move, and no
module imports `legacy/`, which is not packaged. `tests/test_imports.py` enforces
both by AST.

## Rules that span modules

**The tabs are the server's; their order is the browser's.** The tab bar is the
server's open sessions, the same in every browser, so Close on any browser removes
the tab everywhere and nothing accumulates in a browser. Which tab is focused and
the order of the tabs belong to each browser (the URL hash and local storage); the
server never sees them.

**A session's card is published when it changes, at most four times a second.**
Status, handle, title, model and the agent's standing go out at once; activity,
context and cost change on nearly every line of a turn and are coalesced for
250 ms. Publishing them per line doubled the server's cost per line. The
cache counters (`tool_calls`, `cache_recent`, `compactions`) are coalesced the
same way. They count only the main thread's events, like `context_tokens`,
and one usage repeated across the lines of one call counts once (`cache.py`).

**A session's attention is decided in Python, from facts and the agent's own
reports.** Working, error and waiting are facts the server holds: the turn, the
fold's last failure, live monitors, background tasks, held messages, queue tasks,
the approved workflow run it is the main agent of,
and child sessions that are working or waiting themselves, so a wait is
transitive and a change in a child's card re-derives its parent's. The sessions
waited on go out by handle (`waiting_for`), so a change in a worker's card
re-derives its enqueuer's the same way. Whether a finished turn asked the person something, showed
them something to read, or just finished, only the agent knows, and it says so
with `turn_end`; approving a run answers the needs_you of a turn opened before
the click, so the main agent's card falls through to waiting on the run, and
the click wakes it once to tell the person where the run next stops (#474); its plan comes from `plan_update`. `turn_end` refuses done
while the plan has an item not done, because a done card offers to archive the
session and the person should not have to read it to learn the task goes on.
For the same reason it refuses done while a monitor, a callback queue task, a
background task or an approved run will still wake the session
(`Registry.wakers`, #608); its children do not count, since they do not wake it.
Both are aegis records in the
store, so the fold derives a session's `standing` and a refold gives the same
card; the meta keeps it, so boot still reads no store. `attention.py` holds the
precedence. No model reads a transcript to guess what a turn meant. Past the
`context:` block's `handoff_at`, `turn_end` answers with the session's context
size, and the agent passes a `handoff` brief in a second call when its task is
at a break; the answer is a tool result, not part of the system prompt, so it
never breaks the prompt cache. Below that, its answer says what the person has seen from the agent
since the last send (`Fold.shown`, read from the stream, where text arrives
before the call): nothing, or how many messages and how the last begins. A
model that worked its answer out in its thinking believes it was shown, so it
needs the fact; the opening, not a verdict, because a status line before a
tool call is prose too and only the agent can tell it from its answer (#679).
A queue worker hears `noted`, since its final message is its task's result. The brief rides in the report, and
`session.handoff`, which only a person can call, starts the fresh session
from it. The fold also
times the plan from the records' `ts`: work is time inside a turn and after a
turn that did not hand back to the person, idle is the rest, and each item gets
the work done while it was `doing`. It accrues only at a plan record and at turn
boundaries (`transcript/plan_clock.py`); the browser adds the time running since,
so a turn's lines do not rewrite the meta.

**What a person has read is the server's, shared by every browser.** A session
keeps the ids of the agent messages no person has read, and when someone last
read, in its meta, so boot still reads no store; a new message joins the set as
the fold publishes it, and only a person's `session.read` removes ids. The `unread`
flag lives in the transcript channel's view, never in the fold's entries, so a
refold of the store still equals them. A read makes no store record, so it moves
no `rev`: a delta (`Session.snapshot`) also carries every agent message read since
the revision the client holds, and every agent message when that revision is from
before this process loaded the fold, whose earlier reads it never saw. A browser reports a message once its row
has been at least half visible, or has filled half the view, for a second while
the page is visible and focused. A done or review badge clears once the turn's last
agent message is read, which `attention.card` decides as the card's `mark`.

**A recap is aegis talking to the person, paid for once.** A browser that opens a
tab asks `recap.request` once the first snapshot or delta lands, a tab restored
from its cache included; the server decides whether the unread stretch is long
enough, runs one harness-specific one-shot call on the agent named by `recap:` in
`.aegis.yaml` (read-only, no tools, no project instructions, an empty working
directory), and appends the answer as a `recap` record, so every browser receives
the same entry. Nothing
in it is lazy on the wire, and the send that folds it gives it that send's `rev`,
so a tab returning with an older revision gets the folded row. One call
runs per session at a time and a point in the transcript is never paid for twice
unless a person forces it; the call kills its process when cancelled. Nothing in a
recap is sent to the agent. `/btw` is the same one-shot path's second caller
(`oneshot.py`): the agent `btw:` names answers a side question from a window onto
the fold (`btw.py`), while a turn runs too, and the question and answer are two
records, `btw` and `btw_answer`, that fold into one entry and never reach the
agent.

**One registry, every caller.** Every action is one registered operation with a
pydantic params model. A websocket `call` is one projection of the registry, MCP
tools are another, and plugins add operations to it. Nothing reaches the client as
an action that is not an operation, so a subsystem cannot exist for agents and be
missing from the UI. The retired September client died of the opposite: a protocol
in which every subsystem needed its own fields.

**Agents call the same operations, as MCP tools named after them.** An operation
marked for agents is served at `/mcp` under its name with the dot as an underscore,
so `monitor.start` is `mcp__aegis__monitor_start`. The caller is the session whose
token the request carries; the token is minted for each harness process and rides
in the `X-Aegis-Session` header of its MCP config. No tool takes the caller's handle as an argument,
because an argument can be wrong and the token cannot.

**The same operations run from the agent's shell.** aegis exports the endpoint
and the token to every harness process as `AEGIS_MCP_URL` and
`AEGIS_SESSION_TOKEN` (`harness.session_env`), and `aegis call` posts one
`tools/call` with them, so a call from a loop or a pipe is the tool call itself:
there is no second path to keep in step with the first. Every tool's description ends
with its shell form, because that is where a model chooses: the same advice in
the primer alone moved Sonnet to a loop in none of nine live runs, and in the
description in four of four. The cost is that every
child of the agent inherits the token and can act as the agent. That is accepted:
those children already run as the same user with the agent's files, and the
token still dies with the harness process.

**A harness is a module behind one interface.** `Session` asks a `Process`
(`harness.py`) to start, send, interrupt, set and end, and the fold reads each
stored line through the parser its `src` tag names. Claude Code
(`claude/harness.py`) speaks stream-json on stdio; OpenCode
(`opencode/process.py`) is one `opencode serve` per session over HTTP, because its
MCP token, like Claude's, is per process. Its server is of no use once the process that
started it is gone (only that one knows its password) and holds about 300 MB,
so it is started to die with its parent (`launch.child`'s `die_with_parent`,
`setpriv --pdeathsig`), every failed or cancelled start or probe ends it, and
`aegis serve` ends at boot any whose owner, named in its environment, is gone
(`opencode/sweep.py`, #620). Codex (`codex/process.py`) is one
`codex app-server` per session over JSON-RPC on stdio, for the same reason, and
the only harness whose model, effort and sandbox travel with every turn, so only
a new provider restarts it. Its process writes `aegis/*` lines of its own for what
only a response carries (the version, the thread, each turn's model), and kills
its process group after any exit, because a grandchild of a dead child held the
thread's writer lease and the child's pipes. Cursor (`cursor/process.py`) is one
`cursor-agent acp --trust` per session over ACP on stdio: `session/new` hands the
session its own MCP server as a header (`mcpServers`), the only per-session
channel Cursor offers, its model ids carry their effort so `set(effort)` only
confirms one, and it has no cancel — an interrupt closes the turn with its own
`aegis/turn_done` line, clears the queued prompts and kills the child quietly,
while a prompt typed mid-turn waits in the driver, because a second
`session/prompt` would cancel the running one. The parsers emit the same events, so
the entry rules exist once. Token deltas, OpenCode's, Codex's and Claude's
(`--include-partial-messages`), are the one thing folded and never stored: the
part's closing update carries the whole text, so the live view is ahead of a
fresh fold only while a part is open. OpenCode's closing event names the same
step and ordinal as its deltas; Claude's is the assistant line for that block, whose row replaces the one its
deltas drew (`claude/stream.py`). OpenCode's and Codex's parts are stored
opening too, and the fold puts the entry in, empty, at the open, so a prompt
read while a part streams sits after that part, live and on a reload (#373).
Cursor is the exception on purpose: no update of its own ever repeats a block,
so its chunk lines each carry the block's text so far as a keyed part and are
stored — they are the only copy, and a fresh fold rebuilds exactly what
streamed (`cursor/stream.py`).

**Claude Code signs in with a token nobody refreshes.** Claude Code processes
sharing one stored sign-in race to refresh its single-use token, and the losers
are logged out. So every Claude Code process the server starts as itself
(sessions, recaps, side notes) gets `CLAUDE_CODE_OAUTH_TOKEN` from a 0600 file
(`claude/login.py`), read at each spawn, unless the server's environment or the
pool file already sets it. A turn that fails to sign in (`Result.auth_failed`)
raises one card, in the first failing session's transcript, which every later
failure joins. Its field goes to the person-only `claude.token` operation, which
checks the shape, asks Claude Code itself with one `claude -p` in a scratch
config directory, writes the file and restarts the sessions on the card. aegis
runs no step of the OAuth flow. The usage endpoint refuses that token, so each
turn's `rate_limit_event` (`stream.RateLimit`) feeds `QuotaService.observe`, and
once one has, an unauthorized fetch no longer marks the gauges failed.

**Where aegis has a tool, the harness's twin is off.** A Claude Code session
with aegis's tools starts with `--disallowedTools` naming every Claude Code tool
aegis replaces (`claude/process.py` `REPLACED_TOOLS`: subagents, messaging,
cron and wakeups, monitors, notifications, task lists). The primer already says
to use aegis's, but a schema the agent is told not to use still ships on every
request, about 5.9k input tokens a session on 2.1.296, and an agent that reaches
for it anyway works around the transcript and the inbox. An agent keeps one
with `keep_harness_tools`, fixed at spawn like its priming. A session without
aegis's tools keeps them all, since nothing would replace them. The primer is
not trimmed to match: OpenCode and Codex share it and still have their own
twins. The harness's own skills and memory are a person's choice, not the
server's: the owner's `knowledge: native` (their setup record) is read once
at a spawn into `SpawnSpec.knowledge_native`, kept in the meta, so every
process of that session and its command menu follow the mode it began
with; the session passes it as `Launch.knowledge_native`, and each harness turns
its own off its own way: Claude Code adds `Skill` to the disallowed tools
and runs with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, Codex gets `-c` keys and
`--disable memories` (`codex/config.py` `NATIVE`), OpenCode a rule denying
the `skill` action (`opencode/config.py` `NATIVE`).

**A `/` line is resolved on the server, and a typo costs nothing.** `session.send`
runs an aegis command (`commands.py`) as the operation it stands for, passes a name
in the session's catalog to its harness as typed (OpenCode's run through its
command endpoint, or as a prompt with the skill attached), and refuses anything else: Claude answers an unknown command
with the model. The catalog is the harness's own (Claude's `initialize` answer,
OpenCode's `/api/command`, `/api/skill` and `/api/model`), in memory by harness and cwd,
never on disk, so an upgraded CLI never
meets a stale list. `/model`, `/effort` and `/permission` are aegis's, because the
spec the next `--resume` is built from has to change with the process; they reach
a live `claude` as control requests whose answers `ClaudeProcess.request` routes
and does not store.

**The journal is an index over the stores.** What was done on a server (commits,
pull requests, plan items turning done, turns that changed files, and the decisions
and blockers agents note with `journal_note`) is derived from the transcript
stores by one pure `Deriver` (`journal/derive.py`), live through `Host.recorded`
and in a backfill. Rows are keyed by log id, record and ordinal, so the two paths
write the same rows and `aegis journal rebuild` loses nothing. Paths are named by
their repo's main checkout, so an edit in a worktree is found under the main
checkout's path. Git and SQLite run on one writer thread; the event loop only
enqueues, and boot reads no store: the journal starts before the sessions boot,
and `journal.db` records in `PRAGMA application_id` that a backfill finished, so
an interrupted one resumes at the next start. A corrupt `journal.db` is set aside
and rebuilt (#289). Each touch and each commit or pr row also keeps `top`, the
worktree it was in, and a commit row its full `sha`, for the Repos row.

**Usage is counted beside the journal, from the same records.** A second pure
deriver, `UsageDeriver` (`usage/turns.py`), is fed every record the journal's
`Deriver` is fed, on the same writer thread, live and in its own backfill. At
each turn's end it writes the turn and its parts to the usage tables in
`journal.db` (`usage/tables.py`): the turn's cost (Claude's running total's
rise, else its priced tokens) split over its tool calls by token weight, each
part one repo, purpose and activity. A part whose purpose waits on a later
record (a code edit on its commit, a read on the next purposeful call) is
written unsettled and settled later, so live and backfill reach the same rows.
The usage tables keep their own version and backfill mark in `usage_meta`, so a
complete journal that gains them backfills usage only. A part carries its
turn's dimensions so a query reads one table. `usage/query.py` answers every
question with one grouped statement over a window; `aegis usage` prints it.
Repo owners come from each repo's `origin`, cached in `repo_owners`.

**A session's repos are the journal's, their state is git's.** `repos/service.py`
lists the worktrees a session wrote to from the journal's `top` columns, plus the
writes the writer thread reports through `Journal.on_live` before the turn's row
exists, and asks git (`repos/probe.py`) and `gh` (`repos/gh.py`) only for the
present state. Uncommitted counts only the files the session wrote. A worktree is
probed at most every 10 s, in a thread; only sessions a browser subscribes to on
`repos:<log_id>` are built and published, at most every 5 s and only on change
(#526). The server sends one line per worktree; the browser groups them per repo
(its main checkout) and names each peer once per repo with the checkouts it wrote
in (#595).

**The inbox holds messages until a turn ends.** A monitor's wake, a queue's result
and a handoff reach a session as a user turn headed `> from <kind>:<name> · …`. An
idle session gets it at once and an asleep one is woken for it; a working one
holds it and gets every held message as one turn when its turn ends. Writing to a
working `claude` would inject the message at its next tool boundary instead, which
is what an urgent handoff asks for: `deliver(urgent=True)` sends it into the turn
when the harness declares `steers_mid_turn` (Claude Code, Codex), records the send
as `steered`, and holds it otherwise. While messages are held, the `held` notice
source gives the busy agent one warning per message, its sender and first line, on
its next aegis tool result; the warnings end silently when the batch is sent.

**aegis nudges an agent that drifts, once.** A live, idle session that is
not a queue worker, whose card says done or review, gets an inbox message
headed `> from aegis:nudge · plan|idle · …` when its plan has gone
`nudges.plan_min` (15) minutes of work untouched with items not done, or when
its last turn ended with no `turn_end` `nudges.silent_s` (2) seconds ago
(`nudges.py`, `.aegis.yaml`). The silent check is scheduled from the turn's
end; a 15 s tick is the backstop and sends the plan nudge. What a nudge needs is folded into the
standing from the store (`ended`, `plan_mark`, `nudged`), so a restart neither
repeats one nor forgets it. A send that carries no nudge re-arms the idle
nudge and only a plan record re-arms the plan nudge, so a nudge's own turn
never earns another and nothing loops.

**A notice rides on what a session already receives; it never starts a
turn.** `Notices` (`notices.py`), built beside `Nudges`, tells every live
session once per episode that the disk, memory, CPU or quota is short, or
that a person or an admin's agent announced something. It has two delivery
points: `Session._send` appends the pending blocks to the next send (never to
a `/` command), and `OpTool.run` appends them to the next successful aegis
tool result, unless the call came through `aegis call`, whose output is
parsed. A notice never goes into the system prompt: a changed
`--append-system-prompt` would invalidate a resumed session's prompt cache.
What each session was told is `notices_sent` in its meta, so a restart
neither repeats a notice nor forgets its clear; the episodes and the one
asker per kind and owner live in memory. A 30 s loop turns the episodes so a
notice at critical toasts while nobody is talking to anyone.

**Knowledge is per principal, on the server, found per message.** Each
principal's skills and memories are Markdown files under
`<state>/knowledge/<principal>/`, a git repository in which every change
through aegis is one commit (`knowledge/`). The state directory is out of
every member's reach, so ops read and write it for the caller's own principal
(`App.who`), and a name another principal holds answers like one that does
not exist. `Session._send` asks `Host.knowledge_hint` for a block before the
notice blocks; the registry ranks the session owner's items (or the admin's,
for a session from before owners) against the message with BM25, built in
memory per principal and rebuilt when the store's version moves, and writes a
`hinted` usage row per item to `<state>/knowledge-usage.sqlite`. The block
uses the notice header, so the fold already gives it its own row, and like a
notice it never goes into the system prompt or onto a `/` command. Loading a
skill (`skill_read` of its SKILL.md) is a fact about the session:
`Session.skills` lists the loaded names in order, in the meta so a restart
keeps them, and the card shows them. A skill's scripts (`skill_run`) and its
MCP server (`skill_mcp.py`) run as the session's Linux user in a fresh copy of
its folder, which the server pipes to the launched shell as a tar, since the
store is out of a member's reach. A skill's MCP tools stay behind the one
`skill_tool`: the endpoint is stateless and Codex does not re-read a tool list
mid-session by default, so no tool is added to it per session. What an agent
saves unasked is `status: proposed` in the file itself, so the index, the hint
and the command menu need no other table to leave it out. `asked` counts only
when the session's current turn began with a person's message:
`Session.person_turn`, set by a send that starts a turn from a typed message
or a person's `/` line and cleared by any other, in memory only. An item saved
approved because of it gets an Undo card, as every agent change to an
approved item does. A proposal's card and the Undo cards are transcript records
like config_suggest's: the record carries what the card shows, the commit Undo
reverts and a digest of the proposal's files (a skill's whole folder), so the buttons work after a
restart and Approve never approves a later text the person was not shown.
The card ops act on the session owner's store and are refused
(`not_owner`) to anyone but that owner, an admin included. Expiry and staleness flags (`knowledge/checks.py`) are
computed on a save (in the background, so a hung read never holds or fails
a committed save) and in a daily pass the App runs from boot, each bounded
at 30 s per principal, through the
principal's `as_user` runner, so the paths a memory quotes are looked for as
the member and never on the event loop; they live in memory in `Knowledge`
and in the usage db's `flags` table, so a restart logs no flag twice.
The Knowledge view's ops (`knowledge.list`, `.get`, `.put`, `.history`,
`.restore`, `.remove`) are a person's, never an agent's, and act on the
caller's own store, an admin's included. A person's edit is saved approved,
guarded by a stamp: the SHA-256 of the item's file when the view read it,
checked under the store's lock, so an edit never overwrites a change it did
not see.
`/setup`'s import (`knowledge.import`, `knowledge/importer.py`) reads each
ticked source on the session user's `as_user` runner, so a member's files are
read as the member and never on the event loop, and saves it with `fresh`,
which refuses a name any item holds. The ticks are the approval only in a
turn the person began (`Session.person_turn`, which `/setup`'s line and an
artifact's submit set): then one transcript card lists the items, each with
an Undo that reverts its own import commit; in any other turn each item is
a proposal with its own card. A person calling the op names the session
for its card (`log_id`). The setup record (`setup.Records`) keeps what was imported
and is merged, never replaced, by each write.
The optional embeddings (`knowledge.embeddings`, `knowledge/embed.py`) only
ever add to BM25: an index built in a thread holds each item's vector, and a
rank given the query's vector fuses the two ranks (k = 60). The model loads
and encodes only in threads, its vectors cached on disk by content hash under
`<state>/knowledge-vectors/`. The hint on a send never waits for it: the
first index built in place on a send has no vectors, and a send uses the
query's vector only when it is already in memory, else BM25 alone while the
text is encoded in the background. Search tools await the vector. The
default path imports nothing outside the standard library.

**A session's own status is a notice its subsystems push.** Beside the host
kinds, `Notices.set(log_id, source, items)` takes per-session items from a
source: the plan (`nudges.py`), monitors (`monitors.py`) and queue tasks out
(`queues.py`). Each source sets its items when its facts change; `pending()`
only reads them, so it stays cheap per tool call and a source may later live in
a plugin's process. A `warn` or `critical` item rides like a host notice, once
per level, with its own clear text; `info` items from every source make one
`status` block that rides on prompts only, when its lines changed. An item with
`wake` (the monitor warnings: stalled, broken, overdue) is delivered at once
through the inbox to an idle owner, or at its turn's end, so a waiting agent is
not left asleep until a monitor's timeout. What a session was told of these is
`items_sent` in its meta, apart from `notices_sent`, whose keys the host kinds'
restore reads; a compaction empties it so the fresh context is told again.
While the server boots, `Notices` holds every per-session notice until each
source has set its items (`boot_begin`, `boot_end` in `App.boot`), so a
restart's continue neither forgets what a session was told nor says it tracks
nothing; a monitor warns only after its first check in the new process, so
downtime never reads as a stall. Several wakes due at once go as one message.
Sources declare their `.aegis.yaml` keys in `notice_sources.py`, a module that
imports nothing from aegis, so `notices.py` and `config.py` name no source:
that call is the seam a plugin will use (#490).

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
queue worker is finished only when its turn has ended with no live monitor, no
held message and no Claude task still open (`task_started` without its
`task_notification`), and no session it spawned still working. Reading the
turn boundary alone as done closed a worker mid-wait in the legacy tree. The
test is `Registry.finished`, and a schedule's run ends on it too.

**A schedule's tick passes four layers, and its state is folded from a log.**
`schedules.py` runs one clock task in the server. It wakes at the next tick or
after a minute and compares wall time, because asyncio's sleep stops while a
machine is suspended. A tick goes through the overlap rule, the bash check and
the fire. The fire waits under the schedule's own quota lines, the weekly window
on pace by default, judged by `quota_gate.decide` like a token's policy: an
admin has no policy, and a forgotten schedule must not spend the week. Definitions are `<state>/schedules.json`; everything that happens is
`<state>/schedule-runs.jsonl`, and boot folds the live runs, the held tick and
the errors in a row from it (`schedule_log.py`), so nothing is stored twice. A
run ends on `Registry.finished`, the test a queue's task ends on, which also
waits for the run's working children, and while its session owns a running
workflow run it stays live too: a workflow's waits leave its main agent idle,
and the close rule must not archive it. A workflow armed and waiting for a
click does not hold it, since nobody may be there to give the click. A run with `spawn: false` is refused the
operations that start work in `ops.Registry.call`, so the rule holds for every
harness. The view, `js/schedules.js`, draws what the `schedules` channel sends
and edits through the same operations agents use.

**A workflow is a plan the server runs, and only a person starts it.** An agent
arms a YAML tree in which every node says what it does (`workflow_lang.py`);
approving it is an operation not marked for agents, so no tool reaches it. The
engine (`workflows.py`) runs each node with what aegis has (an agent node is a
quiet session spawned under the main agent, locked while the run lives: its
`deliver`, `send` and `interrupt` refuse every caller that does not pass
`through_lock`, which only the engine, the node's own monitors and queue
tasks, and the quota pause do) and appends every event to `<state>/workflow-runs/<run>.jsonl`,
which boot folds back (`workflow_log.py`), as the schedules do. Like their
clock, the engine is level-triggered: each pass looks at every live run, and
a missed hook only delays a step. Only leaves and a map's items are logged;
every seq, par and map state is derived from them on each pass, and a node
inside a map item is named by its instance path (`images.base.impl`). A node
starts only when it has room under the run's `limits` and the server's
`workflows: {max_agents}`, in total and for its agent alias, counted from the
registry's open node sessions and every session spawned under one (a spawn
past the bound is refused), so the bound holds for processes; a judge probe
is such a session too. Commands (`sh`) and polling probes are asyncio tasks
the engine owns, never awaited by a pass, each in its own process group; a
command running in the log with no task after a boot was cut by the restart
and is never run again (a crash's leftover is ended by pid and start time
first). A step's `progress` command runs beside it, after each failing probe
of a check or on a timer while an sh runs; its number lives in memory for the
card, never in the log. An if's branch, a loop's rounds and each until are logged once, so a
restart never asks a probe again. Ids are scoped per map item, loop round and
top level, and a block (`ref`) is expanded at parse. A node's result is a `workflow_result` call
checked against its schema, never its last message. What a run waits on (a
failed step, a gate, a worker's question, the outcome of an edit) is folded
into its open items and told to the main agent once: a `woke` record follows
the delivery, so a crash between the two tells it again and never loses it.
A person's rejection of the plan wakes the main agent as an ending does, and
a person's gate opening or passed wakes it as news with nothing to answer: a
record that tells news older logs never told carries `tell`, so a restart on
an old log stays quiet.
Answers are records too, and a failed branch keeps its cap slot until it is
retried or skipped. The answers only a person gives (a `by: person` gate,
applying or rejecting an edit, approving, handing a gate over) are operations
not marked for agents, and no agent answer stands in for one: a failed
person's gate, or a group around one, is retried or fails the run, never
skipped, and `workflow_stop` refuses a gate. The person may hand a gate's
step to the main agent (`gate_delegated`, by template key), and from then on
each opening of it is a `by: main` gate. A live edit is linted against the progress derived from the log by a
pure function (`edit_errors`), then waits for a person's approval of its
diff. A pass, a stop and an answer hold one lock, so a run's ending is
written once. The main agent is the session that armed the run or
whichever session a handoff continued it in. The card under `workflow_arm` is a `workflow`
entry the browser fills from the `workflows` channel, at most once every
250 ms per run, redrawing only the runs a patch carries; a run whose wire
raises is logged once and goes out with the next flush another run arms, so
a broken state is never retried on a timer of its own. Each story line a
record about a node tells carries that node's instance, so the card folds a
long map's lines with its rows. It is one more
canvas pane kind, `wf-<run>`: it docks through `layout.place`, opens in the
viewer and moves windows through the paths pages and files use, and a run
armed again before approval takes its predecessor's pane. A node's session
stays without a tab while its run lives: `session.unquiet`, `session.stop`,
`session.restart`, `session.close`, `session.configure` and `session.rename` refuse it (once its
step is done, the refusal names the later steps that may still continue in it),
and the card and the Fleet open it read-only. A step's `see` is a fixed path,
resolved on the server from the run and the step by `workflow.see`, an
operation not marked for agents, so the browser never names a path and no
value filled in after approval chooses it. A step with `session: <id>`
is prompted again in an earlier step's session, through the lock and with no
new slot, and is tagged with that session while it runs. The session stays
open while a user may still run in it (a loop its owner is not in counts
until its last round), and a sweep after each pass and at the run's end
closes it once every user is done, skipped or in a branch not taken.
Saved workflows are files, read from two folders on every call
(`workflow_store.py`): the built-ins in the package, whose ids are reserved,
and the project's in `.aegis/workflows/` beside the config, with every save
appended to a history that keeps each version's text. Settings → Workflows
reads a saved file through `workflow.read`, an operation not marked for
agents, and imports one through `workflow.save` called by the person, so an
import is a save like any other: linted on the server, recorded as theirs,
and pausing the schedules pinned to that id. A person's
`/workflow` or `/loop` line starts a saved one with no click through a launch
token (`workflows.Launches`). It is minted only inside `session.send`, which
no agent can call, and by the fire of a schedule a person approved; it is
bound to one session, one workflow and its text at that moment, checked
before it is spent so a refused arm can try again, spent by the arm that
creates the run, and kept in memory only, so a restart voids it. An agent
receives it in the launch turn and can choose only the args, checked closed
against the saved schema. A schedule that fires a workflow pins its version
and pauses for a person's approval when the file changes; the fire checks
the version again, so a hand edit is caught too
(`docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html`).

**Agents change only what they created.** An agent reads and messages any session
it can see (and, through `peer_read`, the archived session it continues), and changes only its own monitors, its own session's names and the
tasks it enqueued. A session an agent spawns runs with at most the agent's own
permission, so spawning is never a way to gain power. A session whose
permission is read calls only the operations marked `read`: its own card and
journal notes, reading, waiting on other sessions and `file_send`. The registry checks the
session's current permission on each call, so the rule holds for every harness
and follows a live `/permission`, and Claude Code is told to let every aegis
tool through, because its plan mode refuses MCP calls an allow rule names
(`ops.py`, `claude/process.py`).

**A token is a principal, and a member changes only what it owns.** A server
holds one token per person, linked server or job, each a name and a role
(`tokens.py`): the token in `.aegis/state/token` is the admin's, the others are
digests in `tokens.json`. Every session and queue task records its owner: the
principal that started it, or the owner of the agent that did, so a member's
agent cannot hand work to anyone else. An admin changes anything. A member reads
everything and changes only what it owns; server settings, links, tokens and
spawning on a linked server are for admins. Each operation declares `admin` or
the param that names the session it changes, and `ops.Registry.call` checks both
in one place. An agent acts as its session's owner with the owner's current role,
and a link acts as the token it presented. A token revoked while its socket is
open is refused at its next message, and reads nothing more. Outside the
multi-user mode every harness runs as the server's Linux user, so a member's agent
with shell access can read the admin token: roles stop accidents between
colleagues, not a hostile member.

**In the multi-user mode a principal is a Linux user.** With `multi_user:` in
`.aegis.yaml`, each token names a Linux user (`users.py`), and the server, an
unprivileged user holding `CAP_SETUID`, `CAP_SETGID` and `CAP_SETPCAP` from its
unit, starts every process through one launcher (`launch.py`): behind `setpriv`,
switched to the owner's user with every capability dropped, with the owner's
environment and the pool file's variables. Files the server opens for a person,
it opens on a thread switched to that person (`as_user.py`), so the kernel checks
every access. The state directory is mode 0700, out of every member's reach. A
signal follows the same ownership rule: the launcher tries the signal directly,
then, when Linux denies it, retries through `kill` as the process's recorded
user (or as the server for server-owned processes). Harnesses, terminals,
timeout cleanup, workflow commands and crash recovery all use this path.
member sees only what it owns: one rule, `App.sees`, hides another's sessions,
runs, tasks, schedules, journal rows and toasts at the three places data leaves
the server (an op's params in `Registry.call`, each channel subscriber's view in
`member_view.py`, and the lists ops return), and something hidden answers like
something missing. The host work the mode needs is the help page `multi-user`,
which `/multiuser` hands to the admin's agent as one turn (`multiuser.py`)
(`docs/superpowers/specs/2026-10-21-linux-users-design.html`).
**An agent suggests the configuration; a person writes it.** `config_suggest`
records a card with the whole new `.aegis.yaml`, its diff and the file's stamp
in the transcript, so it survives a restart; an admin's Apply writes that text
through the stamp check (`config.write_text`) and wakes the agent
(`config_suggest.py`). `/setup` hands the help page `setup` to the session's
agent as one turn with the caller's last run, and `setup_record` keeps one
record per principal under the state directory, which a member's agent cannot
write itself (`setup.py`, `docs/superpowers/specs/2026-10-22-setup-onboarding-design.html`).
Closing is the one exception, and it asks for a second thought: `session_close`
closes at once only a session the agent spawned, or a worker whose task it
enqueued, whose attention is done. Anything else, the caller's own session
included, is refused with a prose account of that session (who started it, its
state, attention, plan step and last activity) and a one-time token bound to the
caller and the target; the same call with the token closes it, because a person
may be reading that tab (`confirm.py`).
An agent the person told to close when it finishes asks with `turn_end`'s
`close` instead: the report carries it, and when that turn ends done with the
session and its whole spawn tree finished, the registry archives the tree,
deepest first (`registry._close_when_finished`). Like a handoff's retirement it
keeps no state: the fold drops a report made in an earlier turn.
A person's autoclose (#691) gives that word ahead of time: a flag in the
session's meta, set by `session.autoclose` or `/autoclose` and never by an
agent's tool, under which `turn_end` refuses done without `close`.
Reading includes waiting: `monitor_sessions` watches other sessions' attention
cards and wakes its owner when all have finished or one is blocked on the
person, and `session_list` shows each card and plan. Neither changes the
sessions it watches.
A person's `/spawn` makes nobody's child, but its first message carries the
last turns of the tab it was typed in and that tab's handle, so the new agent
can find the referent of a three-word task (`agent_ops.spawn_opening`).
Across a plain link an agent never enqueues on or reads another server, and
across a trusted one it does all of it (`agent_ops.across`): it hands off,
to `handle@server`, and an admin's agent spawns, as the link's token there, so
the far server's quota policy for that token decides (`app._spawn_far`). Its
permission is capped at the agent's, and the far session records where it came
from as `spawned_from`, provenance the far side cannot check and never trusts.

**A member spends the pool only below its lines.** A policy (`quota_gate.py`)
gives each pool, `claude`, `opencode-go` or `codex`, a mode: closed, open, or
lines, where each listed window of the pool's gauge has a launch line and a stop
line. A member token takes its own policy or `default_policy` from
`tokens.json`, a pool its policy does not name is closed, and so is a token with
neither; an admin has none and is never gated. Below every launch line the owner
may start harnesses; between the lines its running turns go on and nothing new
starts; at a stop line aegis interrupts them. One question decides every start,
`Registry.quota_hold`, asked by `session.spawn`, a queue's dispatch (a waiting
task is skipped, never failed, so the next owner's runs), `Session.deliver`, a
turn's end with held messages, a person's send, and the boot's continue: a
message that may not start a turn is held with `held_for: "quota"`. After every
reading and every minute `App.on_quota` pauses each working session whose gate
says stopped (`quota_paused` in the meta, a `quota_paused` record) and continues
each paused or holding one whose gate is open, with one `> from aegis:quota`
message ahead of what it held. It is level-triggered, so a restart or a missed
reading needs no memory, and only an open gate continues, so a gauge between the
lines leaves a paused session paused and nothing flaps. A reading older than 15
minutes closes the gate and stops nothing: a failed poll must not interrupt work.

**A cascade picks a model at every start and falls only inside a turn.** An
agent's `models:` is recorded on the spawn spec as `cascade` (unless the spawn
overrides the model), and `model` is the one in use, so the position is
`cascade.index(model)` and survives a restart through the meta. `App.start_spec`
is the one place a start picks: every path that starts a harness from an agent
(spawn, handoff, queue dispatch, workflow node, schedule tick) asks it, and it
returns the first model not cooling whose gate is open, or a closed gate; a
model a person moved outside the cascade is judged by its own gate alone. A
turn's failed `Result` is handed to `Host.cascade_next` before the session goes
idle: `cascade.classify` reads the harness's error text, a fall cools the model
(or, for Claude Code's usage limit, the whole pool) and picks the next model
below it, and the session switches with `configure`, records a `cascade` row
and sends a continue prompt under an `aegis:cascade` header in the same turn, so `turn_ended`, a queue task and
a workflow node see one turn ending with the last model's answer. Stop and close
cancel a fall still switching and end the turn once; shutdown cancels it and
leaves the turn working, so the next boot continues it. It never wraps to the
top. The cooling table is in memory per server; one timer at its earliest
end calls `App._release_held`, the same release `on_quota` ends with, so held
queue tasks, schedule ticks and workflow nodes start when a model is free again.

**A link is a client, and nothing travels from a linked server into an agent.**
Alex's laptop must never run what someone else's token wrote, so a link only
asks: it sends calls and subscriptions, and reads back `welcome`, `reply`,
`snapshot`, `patch` and `error`, dropping anything else with a log line. It runs
no request handler, so the linked server has no operation to call here and no
route to this server's agents. The linked server is treated as hostile: an agent
here sees only strings this server composed from far values it checked, a
handle that passes `valid_handle`, a state from a fixed set, whether a handoff
was held, and errors this server words from an allowlist of codes. Never a
title, a transcript, a reply or an error message the far side wrote.
`tests/test_links.py::test_a_hostile_far_server_puts_no_text_into_an_agent_here`
holds it against a far server that puts a marker in everything. The same goes
for the browser: a linked server's sent file reaches this origin only through
`/via`, under headers this server decides from the file's name
(`files.via_headers`), and the client rebuilds every far file link from a path
of that one shape, since a page or a `javascript:` URL running on this origin
holds the cookie that drives every agent here. A socket with no `Origin` is a
program, never a browser, and only such a socket may say it is a link; a link
socket keys subscriptions by `sid` and is not relayed on. "A link" means any
holder of the token that says so: per-link credentials come with per-user
tokens. Before a second person gets a token on a linked server, that server must run
each person's sessions apart and let only a session's owner write to it, or one
person could steer another's session into handing off (the links spec).

**A trusted link is a client both ways, between servers one person alone
uses** (#684). A link added with `--trusted` asks for it in its `hello`, and the
far server grants it in `welcome` only to its admin's own token while no other
token exists there and it links no server of that name (`App.trust_refusal`).
Granted, the near server's link also answers `rcall` with `rreply`, running the
call as its admin acting through a link (`App.answer_link`), the same caller a
link socket is on the far side, so every op keeps one rule for linked calls;
and the far server keeps a `links.Backlink` for the socket, which agents and the
router find by name like any link. The far server checks the rule again before
every call back and ends the trust with a `trust` frame when it fails. A way
back carries calls only: subscriptions are refused (`not_both_ways`), nothing is
relayed to a third server, and the archive merges outbound links alone. On a
plain link an `rcall` is dropped like any unknown frame. Text does travel over
a trusted link: `session_list` passes the other server's entries whole,
`peer_read` reads across it, and `agent_ops.far_error` passes its errors
through, since the far server is this person's own. Acting goes the same way:
`queue@server` enqueues there with the agent as the task's `far_enqueuer`, and
the result comes back through the link-only `queue.deliver`; `session_close`
goes through the link-only `peer.close`, which applies the local two-step rule
with `spawned_from` as the starter; `monitor_sessions` reads the far session
list each interval (`App._far_list`), keeping the last reading while the link
is down.

**The client knows no subsystem by name.** Server state reaches the browser as
named channels: a snapshot on subscribe, then numbered patches. A gap in the
numbers, or a reconnect, means resubscribe, saying which revision it holds; a
channel that keeps revisions answers with what changed after it, and any other
with a fresh snapshot. Adding a subsystem adds operations and channels, never a protocol field.
A subscriber may ask for `merge` on a channel whose rows have a key (only
`sessions` today, `channels.MERGE_KEYS`): it then gets a row it already holds as
the fields that changed, diffed against what that subscription was sent, and
one that asked nothing (an older client or link) keeps getting whole rows.
Whole session rows re-sent for one changed field were about a third of an idle
client's traffic (#525).

**Python decides, the browser draws.** Every fact and decision about a transcript
entry is computed once in Python: its glyph (the name of a symbol in the
client's SVG sprite, `js/glyphs.js`), title, summary, status, the hunks of an
edit, and what collapses. The entry crosses the wire as data and the
browser only turns it into markup. One copy of each fact means no drift, and a
data protocol version fails loudly across a link where mismatched markup would
break silently.

**The wire carries no collapsed detail.** A tool's arguments, output, file view
and diff, a system note's tail and thinking text stay on the server until a row
opens and asks for them with `transcript.detail` (`transcript/wire.py`). They
were 75% of a snapshot's bytes over 80 real transcripts. Every entry carries `rev`, the store
index of the record that last changed it, so a returning client asks for what
changed since the revision it holds, and the client keeps the last 8 tabs it
showed.
A tool's entry holds only the tail of its output (`describe.output_tail`);
its copy button asks for the whole with `transcript.output`, which parses the
store again. So the browser cannot search a transcript alone: the find bar
(`js/find.js`) searches the entries' text it holds and asks
`transcript.search` for the ids whose withheld detail matches
(`wire.withheld_text`), which the module that withholds it defines. Fetching
every row's detail to search it would ship back the 75% the wire saves.

**A renderer is a function that returns a Node.** The client is plain ES modules
with no framework and no build step. A plugin's renderer has the same shape, so
writing one needs no framework.

**A keystroke costs the same in any session.** Every layout in the page costs a
hover hit test and a paint over every mounted node, and a keystroke always lays
out, so nothing the page holds may grow the DOM or the redraws without bound.
The transcript keeps every entry's data but mounts rows only for the tail
(`js/transcript.js`), more as the reader scrolls up, and off-screen rows skip
layout. The `sessions` channel goes out at most every `PUBLISH_EVERY_S`,
merged across sessions (`channels.Throttle`), except a change a person acts on
(a session added or removed, its state, names, model or attention), which goes
at once. The page redraws once a frame, and only the tab and card of a session
that changed. Mounting a long transcript whole and rebuilding every tab per patch
made typing cost grow with the transcript's length and the number of sessions
(#157, #158).

**A sampler runs only while someone watches.** The host meters (`host.py`)
and the network readings (`net/service.py`) read nothing while no browser
subscribes to `host`. The one exception is each session's load (`load.py`,
#762): the cpu and memory notices name a session's own share of the last
minute, which needs samples nobody watched, so it walks `/proc` (or reads
the session cgroups) every 5 s in a thread, about 80 ms for 400 processes. The network loop polls for a watcher once a second and
refreshes the moment one arrives. The network readings reach the internet and
the speed test downloads a megabyte, so a server nobody looks at must not pay
for them. Both ride the one `host` snapshot, so a linked server's band shows
its own.

**A toast is an event, never a state.** `App.toast` publishes on the `toasts`
channel, whose snapshot is always empty, so a browser that connects later never
replays one; it skips the throttle, because a toast must neither wait nor merge.
Every browser draws it with `js/toast.js`, the one function the client also
calls for its own refusals and failures. Something that stays true while the
person looks (a config that does not parse, a send waiting on uploads) is not a
toast: it would leave before it was fixed, so it stays next to what it is about.
An agent's toast also goes to the desktop while the page is hidden (`js/ping.js`),
because the card would leave before anyone saw it; aegis's own never do, since
they answer something done on the page. An agent's toasts are capped at six a
minute per session (#329).

**The service worker keeps one build.** `sw.js`, served from the root with
the build id written in, downloads the files `/client-files.json` lists into a
cache named after the build and serves `/`, `/static/**` and the manifest from
it, so aegis opens with no connection. The page and its scripts always come from
the same build: a new build downloads beside the old one and waits, the page
offers a reload (or takes it at once when the socket closes with 4400), and every
tab the old build loaded reloads when the new worker takes over. Only a complete
build is served; a failed download never fails the worker's install, so
notifications keep working, and the worker downloads again when a page asks,
which every page does when it loads and reconnects. A sign-in
(`/?token=`) and everything outside the client go to the network. Notifications
come from the worker's registration too: Chrome on Android refuses
`new Notification()`, and the worker's `notificationclick` opens the session even
after the page is gone. The constructor remains only for a page without an
active worker, which is a desktop on plain http (#342). The worker also takes
what Android shares into the installed app (the manifest's `share_target`,
#735): a page cannot read a POST's body, so the worker keeps the text and files
in the `aegis.share` cache and redirects to `/#new?share=<id>`, where the New
session form takes them out. `#new?prompt=&server=&cwd=` fills the same form
from a link; the query is read once and dropped from the URL, and nothing is
sent until the person sends it.

**The device keeps what it saw.** `js/devicestore.js` holds the last Fleet and
the twenty transcripts saved most recently in IndexedDB, as the wire carried
them (no collapsed detail, never a token). The Fleet draws from it at boot and
the transcript when a session opens; a saved session is `stale` until its
server's live snapshot replaces it, and a transcript's subscription waits for
the saved copy so that it asks only for what changed since its revision. The
copy of the session the URL opens is read at boot, while the socket connects,
so that wait costs nothing on a reload (#345). Both
are written at most every 2 seconds, only from what a live socket carried, and
notifications follow live sessions only, so a cold start never pings for what
changed while the page was closed. Offline, what needs the server is disabled
(`.needs-net`, `js/net.js`), and a socket refused with 4401 empties the device
(#342).

**A message written offline waits as a draft.** One per session, in the device
store's outbox (`js/outbox.js`); writing again appends to it. On reconnect each
draft is sent with `expect_rev`, the transcript revision its writer saw, and
`session.send` refuses it as `stale` when the session's `content_index` (the
last record a person reads: user, prose, tool, inbox) is past it, so a deploy
or a stop while the device was away does not count as moving on. A stale or
closed session's draft stays and asks, as does one refused for another reason,
with the reason. Every change to a draft is one IndexedDB transaction: a send
marks it `sending` and only the send's answer settles it, so two tabs never
send one twice, a page closed mid-send leaves it to be sent again (with its
revision, so a send that did arrive asks instead of repeating), and text
written meanwhile becomes the next draft (#342).

**A send from the box gets a verdict in 5 seconds and arrives once.** A socket
can stall without closing, so `conn.open` stays true and a call waits forever
while the browser buffers every press; when it recovers they all arrive. From
Send to the answer the box is read-only and the button spins. Each composer
message carries a `nonce`; `session.send` answers a nonce it has seen with the
first delivery instead of delivering again, and forgets one that failed. With
no answer in 5 s, plain text becomes the session's queued draft carrying that
nonce, the box clears, and the page drops the socket (`Connection.drop`, since
a dead socket's own close can take minutes) and reconnects; the draft goes out
on the reconnect and a copy that did arrive is not delivered twice (#429). A
draft's send gets the same deadline, so a flush on a stalled socket puts the
draft back in queue instead of leaving it `sending`. A nonce stays on a draft
only while the draft is that text alone. A command or files cannot wait on the
device: with no answer the box is the person's again, with its text and a line
saying it was not confirmed, and sending it again reuses the nonce; a late
answer still clears the box if it holds the same text. A session on a linked
server gets the lock and the verdict but no nonce, which an older server would
refuse as an unknown field, so its queued draft relies on `expect_rev` alone
(#350).

**Push goes from the server, encrypted for the device.** A device subscribes
from the bell with its sources (`needs_you`, `error`, `review`, `done`, `toast`);
`push.py` keeps the subscriptions and this server's VAPID key under
`<state>/push/` (0600, since an endpoint and its keys are enough to push to that
device). It listens to the `sessions` channel like a browser, and `App.toast`
hands it each toast, so a push is the same transition a page would notify; the
first snapshot only records where sessions stand, so a restart pushes nothing,
and `review` or `done` count only when a turn just ended. It encrypts each
payload for the device (`webpush.py`, RFC 8291, which imports `cryptography`
only when it first signs or encrypts: about 7 MB a server with no subscriber
never pays) and posts it to the push
service from its own queue, so a slow service never delays the channel. Each
subscription has a lane of its own and the lanes run side by side, so a dead
device delays no other; a push too old when its turn comes is dropped, and a
row goes when its service refuses it or fails it five times in a row. Only
endpoints at a known push service are taken (`AEGIS_PUSH_HOSTS` adds hosts),
because the server posts wherever a token tells it. The worker shows a push
unless a window of that device shows the session in front. A device with push
leaves those sources to it for this server's sessions, so one event never
notifies twice; a linked server's are not pushed, so the page notifies them
(#342).

**Folding hides rows, it never unmounts them.** From which fold level a kind
folds is a fact on the entry (`fold`: 0 never, 1 the work, 2 also what arrived
and what was shown; `transcript/entries.py`). The client draws each run of
consecutive entries folded at the reader's level as one line on its first
mounted row and hides the rest, so the mounted rows stay a suffix of the
transcript and the window, the trim and the reader's place work unchanged; the
walks (j/k, the first row on screen) skip hidden rows. The line is counted from
the run's data, not its rows, so a run that starts above the mounted rows still
says all it holds. The latest recap never folds: it is what a reader coming back
reads first, so it shows at every level while the older recaps fold with the rest.

**An action is one row in one registry.** `client/js/keys.js` holds every action
the client offers by key (its id, title, keys and the views where it applies) and
the one `keydown` listener that dispatches from it; app.js gives each id its run,
and a missing or extra one throws at boot. The `?` list and the command palette
(`js/palette.js`, Ctrl+K or ⌘K) are drawn from the same registry, so neither can
name an action that does nothing, and a new action shows in all three at once. No
other module listens for keys on the document. Chrome binds Ctrl+K to its address
bar search but does not reserve it, so the page's `preventDefault` wins; on a Mac
it is ⌘K alone, because Ctrl+K there deletes to the end of a line. Chrome keeps Ctrl+T,
W, N and Tab, and on Linux Alt+1…9 and Alt+←/→, for itself, so the chords are the
Alt keys it leaves free. Plain keys act only outside a text field and the view
decides what they do: there is no mode. A selection, in the transcript or the
Fleet, is held by id and re-marked after every redraw, because both replace their
nodes on each patch. Esc closes, in order, a dialog, the drawer, the ? list, the
find bar and a monitor card or a panel row's card, and only then interrupts.
Ctrl+F outside a text field in a transcript is the find bar's, not the
browser's, whose find misses every row not mounted and every closed detail; a
second Ctrl+F, inside the bar, is the browser's again.

**A theme is one CSS file over one markup.** The markup carries everything any
theme might show, and the base stylesheet reads only CSS variables. A theme sets
the variables and a few overrides that decide what shows. Every font a theme
names is vendored in `fonts.css`, or the theme looks different on every
machine. Anything a theme draws over the whole window, such as Phosphor's
scanlines, is positioned against `#a2` and never the viewport, because the
landing page applies the same rules to a window that sits inside a page.

**The session panel is rows that open cards.** Each section of the panel is a
compact row (`.prow`) that holds its own card (`.pcard`) in the markup, so the
code that fills a row fills its card, and an open card stays current.
`js/side.js` only moves them: on a desktop the card is fixed beside the panel,
level with its row, and opens on hover or keyboard focus; in the drawer a tap
opens it under the row. Usage shows the quota the session spends, matched on the
`harness` each provider carries on the wire, and for OpenCode on the provider
its model names. The panel's width and whether it is collapsed
(`data-side=closed`) belong to the browser, in localStorage, as the theme does.

**One page for every screen.** A phone gets the desktop's markup. Below 760 px
one CSS block wraps the tab bar onto its own row, turns the side panel into a
drawer (`data-side=open`, opened by the panel button), shows the monitor card as a
sheet over that drawer, stacks the Fleet band and draws each archive row as two
lines with Reopen beside them, a tap on the row reading it; under `(pointer: coarse)` touch
targets grow to 44 px and Enter in either composer, a session's or the new tab's, adds a line. The navigator's
buttons are the exception: they draw at 34 px and take a tap 44 px tall
through a pseudo-element, so the whole pill fits on one line of a phone. Hover opens and
closes a card for a mouse only, because a tap fires a leave right after its
click. The transcript's navigator floats on `--composer-h`, the composer's
height, so replies and extra lines push it up instead of sliding under it. A second set of screens would be a second client to keep in step. The
client asks through its own dialog (`js/dialog.js`), never the browser's, and
picks through its own chip (`js/pick.js`, a filterable list under the chip),
never a native select: `tests/test_client_rules.py` fails on `confirm(`,
`alert(`, `prompt(` or `<select`.

**The store keeps raw lines; entries are derived.** A transcript file holds the
harness's raw stdout lines but its token deltas, and what aegis did (spawn, send, interrupt, exit,
close), each with its receive time and its own index. Entries are folded from them
on load, with deterministic ids, so a better summary applies to old transcripts and
a resume reads the same file the live session wrote.

**The patches add up to the entries.** A browser that saw every patch from a
snapshot on holds exactly the session's live entries, and those equal a fresh fold
of the store. A reload and a live view must never disagree. The session tests check
both after every scenario, because the first bug the browser found was a patch that
left out an update the entries had. A delta from any revision, applied to the
entries as of that revision, equals the entries now, live ones included
(tests/test_wire.py checks every cut of the fixtures and 40 earlier moments at
every step of a stream).

**A transcript is keyed by a log id minted at spawn, never by a handle.** Handles
are reused; keying on them once merged unrelated conversations into one file in
the legacy tree.

**The echo creates the user entry, never the send.** Claude Code injects a prompt
sent mid-turn at the next tool boundary and closes both prompts with one `result`,
so a turn is not one prompt and nothing counts turns by counting sends. A sent
prompt is pending until Claude echoes it, and the transcript records the order the
model read things in. An answer takes the pending send whose text it answers,
else the oldest of its kind, because a slash command waits for the turn to end
while a prompt is read at the next tool boundary.

**An echo that is only Claude Code's own wake is an inbox row, never the person's
words.** When a background command or a subagent ends, Claude Code sends a
`<task-notification>` prompt. `describe.task_notices` reads it into a header
(`background · failed · exit 1`), a label (the command's opening comment, its
description, or the subagent's name) and a fold (the command or the subagent's
report, and the output file), and drops its ids. A failed or killed task is an
`err` row. Any other text in the prompt keeps it a user row.

**System notices never start a turn.** Hook, init, thinking-token and task notices
arrive both inside and outside turns. Only a sent prompt or a turn-bearing event
moves a session to working, and only `result` or the end of the stream moves it
out. Promoting a notice to a turn parked old-tree sessions on a read that never
returned.

**Three roots, never `Path.cwd()`.** The CLI reads the working directory once to
build the config root, state root and harness cwd, and passes them down. Nothing
below it calls `Path.cwd()`, and `tests/test_no_cwd.py` fails if something
does.

**A damaged file never takes a session down.** A store line that does not parse is
skipped and counted; a stdout line that does not parse is stored and shown as a
system entry. A line that cannot be written (a full disk) is logged and dropped,
and a stderr line the file refuses stays in the in-memory tail: the readers keep
reading, because a reader that dies leaves the child blocked on a full pipe and
the session frozen until a restart.

**Agent text is untrusted.** Markdown renders with raw HTML disabled, and the
localhost port is not trusted either: the websocket needs the server's token and
its own origin, because any page in the browser can open a socket to localhost.
The browser holds the token only as an HttpOnly cookie, named per state root,
that no script on the page can read.

**Markdown is drawn for the width it has, and code is coloured only near the
view.** Two markdown-it renderers, configured alike, draw every Markdown body:
one for agents' text, and one for the person's messages that keeps a typed line
break. A table never breaks a word: it scrolls inside its row with its first
column pinned, and under 600 px of width it draws as one card per table row.
The container query that decides this sits on the transcript's scroller `#tr`,
the viewer's `.vbody` and the Help tab's page `.hpage`, never on a row: a size
container inside the
scroller stops Chromium's scroll anchoring, and the text being read jumps as
rows above it draw. highlight.js is vendored as its core and one file per
language behind `js/highlight.js`, which imports the core and a language the
first time a block within 600 px of the view needs them, and caches each result
so a reply re-rendered by every delta does not flash plain. A fence whose
closing line has not arrived stays plain, and so does one whose file fails to
load; the next block asks for that file again. The viewer colours through the
same loader. Copy buttons sit on code
blocks and tables, never on a whole reply; a table copies as its Markdown lines
and as an HTML table at once (#323).

**A tool row says what its views are; the browser lays them out.** The fold
names a tool row's language (`describe.tool_lang`), turns a Read's numbered
output and a Write's content into a file view (`detail.file`), and gives an
Edit hunks with three lines of context, capped at 8 KB: Claude Code's own
`structuredPatch`, numbered in the file, or `difflib`'s, numbered within the
edit. All of it is lazy. The browser colours the views and lays an edit out
side by side when the row is 680 px or wider and in one column below, from the
same hunks, marking changed words by an LCS over tokens (#323).

**A path in a reply is a link, checked when it is clicked.** The primer asks
agents to quote a file relative to the config root or absolutely. The fold
lists the inline code spans of a reply shaped like a path (`detail.paths`);
it never touches the filesystem, so an archive's links do not depend on the
machine that reads it. A click sends the reply's id and the path's index to
`file.peek`, which resolves it as written when absolute, else under the config
root and under the session's cwd, copies the file and returns its card record;
the viewer opens it scrolled to the `:line` and marks it. A relative path that
names two different files, one in each place, opens neither: it answers
`ambiguous_path`, and the person picks with a button that sends only `root` or
`cwd`. Agents quote from their own cwd, so taking the root's copy of a
`README.md` would open the wrong file. A linked
server's replies draw no links: the file would be on the other machine (#323).
The copied file is recorded back on the reply under that path's index, with a
pane key derived from the reply and index, so Dock can keep it beside the
transcript and a reload can rebuild that pane without adding a second card to
the transcript. Opening the link again advances that pane's version in place.
The mechanism stays for links in replies; it is not what keeps a person from a
file, since the Files view opens any file under the root (next paragraph).

**A person opens any file under the root, and only a person.** The Files view
(`fs_ops.py`, `client/js/files.js`) reads and writes files by path, relative to
the config root, through `fs.*` operations. Three rules hold it. Every path goes
through `fs_paths.resolve`, and no other code builds a path from a client
string: no `..`, its real path (symlinks followed) inside the root's, never
inside the state root (it holds the admin token and the live stores) or a
`.git` directory. Every `fs.*` operation is `local` and none is `agent`, so
`Registry.call` refuses an agent with not_for_agents and a caller acting
through a link with not_across_links, in one place; the view polls by calls,
never by a channel, because a link socket may subscribe to any channel. And a
save carries the sha256 the buffer was based on: when the disk's bytes no
longer have it, nothing is written and the answer is changed_on_disk with the
disk's text, because agents write the files a person edits and
last-write-wins would drop an agent's edit silently. A write goes through a
temporary file in the same folder and `os.replace`, keeping the mode, and is
logged with its author in `<state>/fs-log.jsonl`. Admins and members alike may
write: every harness already runs as the server's user, so a member's agent
could anyway. The picker's list is `fs_index.py`'s git-aware walk, run on
demand and never watched (#458). The tree lists one folder per `fs.list`, with
one `git check-ignore` for the folder's innermost repository; a folder holding
a repository shows although its parent ignores it, or `repos/` would hide every
repository a workspace keeps there. A delete is a move into
`<state>/trash/<stamp>-<id>/` beside a manifest of where the entry came from
(`fs_trash.py`), since a file outside git has no other way back; a restore
refuses with taken when something new is at the path, and the boot purges
entries older than 30 days. The trash sits under the state root, so the view
cannot reach it. The open file follows the disk by `fs.stat` every 2 s while
the view is on screen: an untouched buffer reloads, an edited one gets the
conflict banner. Bytes an element fetches itself (an image, a frame, a
download) cannot carry the socket's token, so `fs.ticket` and `fs.read` hand
out `/fs/<ticket>/<name>`: 128 random bits mapped to a relative path, in
memory, until ten minutes pass without a use (a playing video keeps asking
for byte ranges). The route resolves the path again on every request, so a
file swapped for a symlink out of the root after the ticket was issued is
refused, and answers with `files.headers`, the sandbox a sent file gets, with
`no-store` because the file changes. Kinds are `files.classify`'s, so a file
renders the same whether an agent sent it or a person opened it. `file.peek`'s
answer, never its stored record, carries `root_path` when `fs_paths.resolve`
passes the file, which is what the viewer's Open in Files keys on.

The candidates are wider than files: a folder, `owner/repo`, `owner/repo#n` and
`github.com/…` spans are kept too (`aegis/refs.py`). What each names is asked
when the reply nears the view, by `path.stat`, one call per reply, which
resolves it the way `file.peek` does and answers file, dir, missing, repo or
code; a local path wins over a repo of the same name. The builder still reads
no disk, so a transcript rebuilt from its log draws the same. Bare URLs carry
an icon from a fixed set in the sprite and a short label: no favicon is
fetched, so a link an agent writes sends no request anywhere (#393).

**One icon set, in the theme.** Every icon is a `<symbol>` in the sprite
`js/glyphs.js` installs once per page, drawn with `currentColor` (and
`--knock` for a mark knocked out of a badge), so a theme recolours all of
them and no font decides their shape. Code asks for a symbol by name:
`icon()`, `iconText()`, a `*_ICON` table, `<use href="#g-name">` in markup,
or a glyph name Python sends. A family is chosen by a value: `f-<kind>` for a
file, `h-<harness>`, `e-<effort>`, `p-<permission>` for a session's facts,
through `factIcon()`, which draws nothing for a value it has no symbol for.
The help page `icons` lists each symbol with its meaning and where it shows,
and `tests/test_glyphs.py` holds code, sprite and page to the same set (#644).

**A sent file's URL is its key, and its content runs nowhere near the token.**
`file_send` copies the file into the state root under 128 random bits, and
`/files/<id>/<name>` serves it to anyone with the link, because `<img>`, a new tab
and a download cannot carry the token the websocket needs. HTML, SVG and XML are
served with `Content-Security-Policy: sandbox` (HTML with `allow-scripts`, so a
report still runs) and framed with `sandbox`: on aegis's own origin their script
could open the websocket, which the browser signs in with its cookie, and drive
every agent; the sandbox's opaque origin fails the socket's origin check
(`files.py`).
One `file_send` is one record and one card, however many files it carries.
Every file is checked before the first is copied, and a copy that fails anyway
takes the earlier ones with it, so a refused send leaves nothing in the state
root (`files.store_all`).
A Read, Write or Edit row carries the path its tool used, and the person can
ask for that file (`file.peek`): it is copied the same way, as it is at that
moment, and its card opens inside the row. The agent never sees it.
Open natively runs the desktop's opener on the server, so it is a person's
operation only, and only for a socket on loopback to a server with a desktop: a
proxy's public name is a browser elsewhere, and an SSH tunnel to a headless box
looks local but has nowhere to open the file.

**An attachment is a sent file going the other way, and only a person sends
one.** A person's files upload over the websocket as base64 chunks
(`attachment.begin`, `attachment.put`), so they need no route, no auth path
and no relay of their own: a call that names a linked server already goes
down the link. They wait staged in `<state>/inbox/<log_id>/.staged/` and the
agent never sees them there; `session.send` checks every upload complete,
moves each to `<state>/inbox/<log_id>/<YYYYMMDD-HHMMSS>-<name>`, copies it
into the sent-files store (a copy, because the agent may edit its own), and sends the typed text with one line per file.
The person's row shows the typed text and one card, never the paths. No
operation for it is open to agents. Its stored copy has a file-id pane key, so
its card can Dock and the canvas can recover it from the person's row after a
reload. Each harness is started with read access to its inbox, and a chunk's
bytes never reach the log (`attachments.py`).

**An artifact is a sent page with a way back, and the way back is the bridge.**
An agent's interactive page (`artifacts.py`, `artifact_ops.py`) is served and
framed exactly like a sent HTML file, so its script runs in an opaque origin
that the websocket refuses. Its only path to the server is `postMessage` to
the host page, whose bridge (`client/js/artifacts.js`) answers only windows it
mounted and calls four person operations with the artifact id the frame's row
carries, set after the page's own parameters so one the page sent never wins;
the probe's answer is the fifth operation and names no artifact. Every payload
is capped (state and data by size, a label by length, an error by truncation
on both the wake and the probe) and emits are rate-limited, because the page
is agent-written code whose answers land in the agent's context and on disk.
A page's answer reaches the agent through the inbox like a monitor's wake, so
a click mid-turn is held. A page lands only after the static checks and, when
a browser has the transcript open, a hidden run there (`Board.probe`): a page
that throws is a tool error, not a card; with no browser open it lands with
`started` unset, and the first browser to mount it reports an error to the
inbox. Every artifact's bar, on its card, in the viewer and in a pane, has a
Phone width toggle (`entries.phoneButton`): it narrows the frame to 390 px so
the page's own media queries answer as on a phone, and the choice is a
per-tab set of artifact ids, so every frame of one page agrees and a redrawn
stage keeps it. It is client-only; agents are told to lay pages out for both
widths because the person will look at both.

**A secret crosses between an agent and a person without a transcript holding
it.** `secret.send` reads the agent's file on the server, keeps a random pad of
the same length in memory only (`secret_store.py`) and records the XOR on the
card; `secret.reveal`, a person operation, hands the pad to the browser once
and forgets it, and the browser undoes the XOR in the page's memory. Expiry and
a restart forget pads too, which turns every stored copy of the card into
noise. `secret.request` puts a password field on a card; `secret.provide`
writes what the person typed with `O_EXCL` and mode 0600 to a path that must
not exist, wakes the agent through the inbox, and the registry removes that
file on close if its inode is still the one written. Neither person operation
is open to agents, and the call log masks a `secret` param as it masks
`token`, so a value typed on a linked server's page is not logged on either
side (`secret_ops.py`).

**One menu, built on demand.** `client/js/menu.js` owns the page's one open
context menu and its one selection bar. A view attaches a provider to a root
element; on a right-click, a long press or the menu key, the provider turns
the element pressed into groups of items, built from the calls and buttons
the view already has, or null to keep the browser's own menu. Nothing watches
the page while no menu is open: no MutationObserver, and a view that redraws
calls the menu's checks (`retarget`, `dropDetached`, `unbarHidden`) from its
own redraw path. An item's `run` is called after the menu closes, so a dialog
it opens or the focus it moves is not fought by the menu.

**A page proposes what the person points at; only the host acts on it.** Every
HTML preview, a sent page's, a Files page's and an artifact's, is served with a
`<script src="/static/js/pagepoint.js">` added before its `</body>`
(`web.inject_point`); a download is the file's own bytes. A tag, not the script
inline, because previews are cached as immutable and the static script follows
the client's version. In the frame it reports a right-click, a long press and a
selection, with a locator (heading, id or CSS path) and the text, as an
`aegis/point` message; the host bridge takes that one method from any frame it
mounted and draws the menu or the selection bar in its own document
(`app.js`, `pagePoint`), only while the focus is in that frame. A page's script can still take the focus and make a menu
appear; it can never put text in a composer, because that menu takes no
focus and only a click on one of its items writes, never in its first half
second, since the page chose where it opens. A field, a link, an image
or a press with no text keeps the browser's own menu, and a scroll or Esc in
the page closes what the host drew for it, as does any press in the page or
the page losing the focus to another frame.
`/via` adds the tag too, once, so a far server older than this one gets it,
and passes a download through as one. A page whose own CSP blocks the script
simply has no menu. Comments on files and pages live in
the browser's storage until sent, and the server never sees them
(`client/js/comments.js`).

**A card's Open shows its file or its page over the transcript, and a page
runs in one place.** The viewer (`client/js/viewer.js`) covers the session's
column, transcript and composer, and leaves the tabs and the panel in reach;
each session keeps its own across a tab switch. Markdown and text are fetched
whole, since the card carries only an excerpt, and drawn in the theme: text is
coloured through the transcript's loader (`js/highlight.js`), in the theme's
quiet palette (`--h-*`). Anything else is the card's own preview at full size, so a
frame keeps the sandbox the card decided. A live artifact opened there leaves
its card a line instead of a frame (`entries.hold`), because two frames of one
page would each take the person's answer and only one would get the agent's
state.

**The canvas is a record, and a docked page still runs in one place.** A
session's docked pages, files and workflows (`layout.py`) are a `layout` record that the
fold keeps as standing state and the session's card carries to every browser.
Files copied from input attachments use their immutable file id as the pane
key; reply-path peeks use the reply and path index; `file_send` uses its
source-path key. A later copy of either repeatable kind updates the existing
pane.
The person's moves and the agent's tools go through one placement rule, and a
person's operation names the move (`layout.dock`, `undock`, `resize`), not the
result, so a drop never overwrites what the agent docked while the pointer was
moving. The fold takes an ended page off the canvas without writing a record,
and `Session.layout` trims any id that can no longer dock, so a stale id never
blocks a move. Each browser tab draws one window of the canvas: the session's
tab draws window 1, a tab at `#w=<n>:<key>` draws window n, and a page runs only
in the tab that draws its window while every other place holds a line, as the
viewer's hold does. Panes are placed by numbers inside one container and never
moved in the DOM, because moving an iframe reloads its page. A file sent again
from the same path is a new version of one file (`layout.file_key`), and its
pane follows the newest.

**Audio never leaves the browser.** Dictation transcribes in the page, with
Cactus Whistle's WebAssembly build in two workers (`js/dictation.js`). The client
cuts speech into provisional pieces of 4 to 8 s whose text lands at once, and
every 20 s or so re-transcribes the stretch as one final piece that replaces it;
a piece with under a second of voice is never transcribed alone. The server
keeps the pinned engine and model, downloaded once from Hugging Face and checked
against their sha256, serves them at `/dictation/<pin>/` with an immutable cache
header, and answers `dictation.prepare` with that base and the keywords that bias
the model toward names it knows (`dictation.py`). Nothing carries samples, so
dictation works through any proxy that serves the page, and the server pays
nothing per word. A send from a box that got dictated text says so
(`dictated`), and the server adds `dictation.NOTE` after the text for the
agent; the fold strips that exact line off the person's row and marks it
`detail.dictated`, so the transcript and the harness keep the same text.

Reading replies aloud follows the same rule the other way: the reply is already
in the page, and it is spoken there (`js/speak.js`). The browser's own
`speechSynthesis` is the default. Phonon, the opt-in, runs `phonon-tts` in a
worker from files the server pins, checks and serves at `/speech/<pin>/` as it
does dictation's (`speech.py`, `speech.prepare`), and only after a speed check
in that browser finds it faster than real time. A reply is read when its entry
arrives whole or stops streaming (`Transcript.onReply`), never on a resume.

**The help pages are the shipped contract for how aegis is used, checked
against the code both ways.** They are Markdown in `src/aegis/help/`, inside the
package, so a release carries the help for exactly the code it runs; the server
indexes them at boot (`helppages.py`) and agents reach them with `help_search`
and `help_read`. Agents write them by hand, in the change that changes the
feature, because a page has to say when and why to use a tool, which no
docstring dump does. `tests/test_help_pages.py` and `.rift.yaml` hold them to the
code's names: every agent tool, slash command, CLI subcommand and config key is
on a page, and every one a page names exists. Neither reads the prose, so a
wrong sentence about a real tool is for a person to catch on the smoke tour.
What each person has been offered, whether they started a tutorial and the
last release they toured, is kept per principal on the server
(`helpstate.py`), so the new tab's hints are the same on every device; a
principal is decided new or returning once, at its first read, before the
guide it may be starting spawns a session of its own. The release notes ship in
the wheel beside the pages (`changelog_read.py`), and every added or changed
entry ends with the help page that shows it, so `/whats-new` tours the notes
of the code it runs and its guide opens the right page for each.
Each page also declares one to three hints in its front matter, which the new
tab rotates one at a time and which open the page when clicked; a page without
them does not load, so a new page cannot skip them. The new tab's doctor row
reads the last run of `doctor_watch.py`, which runs the same checks as
`aegis doctor` in the background (after the start, on every change to
`.aegis.yaml`, every 6 hours), because a run probes each harness for up to
20 seconds and opening a tab must not wait on that. `/doctor` asks for a
fresh run and starts a session with its report.

**Performance is measured on every PR and never gates.** `scripts/bench.py`
replays a recorded transcript through the fake harness in
`tests/fake_claude.py`, and CI compares the PR's base and head on the same
runner, twice each in the order base, head, head, base, so neither pays for
running second. Each figure is a median of samples taken after a warm-up and
the JS heap is read after a forced collection: single samples moved 40 to
140% between identical runs. A metric warns when it is 20% worse and head's
samples are worse than base's in 90% of their pairs. A regression is a warning a reader
has to weigh.

**A harness version outside the tested range asks a person and tells everyone
else.** Each release ships `data/tested-harnesses.toml`, a min and a max per
harness that the canary passed at. `Session._check_version` reads the binary's
`--version` just before a process starts, cached by its resolved path and
mtime so a launch pays for it only after an upgrade, and judges it
(`harness_versions.py`). A launch nobody is watching (an agent's spawn, a
queue worker, a schedule, a workflow node, a parked session's wake, the
restart's continue) starts anyway and records an `untested` notice, once per
version per session, which the transcript shows and the journal derives a
row from. The verdict of the last launch, when it is outside, is the
session's `untested_version`, which the panel's harness row shows. A person's spawn, `/spawn` line or send to an asleep session that did not park, Wake of one, and Restart are
refused first with `harness_untested`, whose error data the browser turns into
a dialog, and the second call says once or always (`App._version_gate`). The
split is by who is there to answer: a refusal nobody can answer would stop a
nightly job over a warning. Nothing in it can stop a session for good, and the
startup check only logs, because a server that will not start is worse than any
version mismatch.
