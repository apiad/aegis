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

**A server may link others, as their client.** A link (`links.py`) is one
websocket this server opens to another, saying `hello` with that server's token,
exactly as a browser does. The people on this server see and drive the linked
server's sessions through it: a browser message naming another `server` goes
down the link and comes back stamped with it. Links are kept in
`<state>/links.json` (mode 0600, never `.aegis.yaml`, which is in git and read
by both machines), and `aegis link add` or an edit to the file takes effect
while the server runs. Plugin hosts come later, in the order the vision spec
gives.

**A session outlives its process.** A session is live (a harness child, `claude` or
`opencode serve`, is running), stopped (none is), or archived (closed: no process,
no tab). A prompt to a stopped session starts the harness again on the same
conversation (`claude --resume`, or `opencode serve` and the same session id),
which prints nothing old, so the store and the fold simply continue. Only shutdown, Stop and Close end a process. Nothing
reaps idle sessions, because a Claude session waiting on its own background task
wakes itself when the task ends, and stopping it would kill the task.

**Boot reads meta files, never stores.** Each session has a small JSON meta next to
its store, written by write-then-rename. Boot reads only those, so an archive of
hundreds costs hundreds of small reads; a missing or damaged meta is rebuilt from
its store and the session lands in the archive. Boot writes to no store, except one
`server_stopped` record for a session whose meta says it was mid-turn.

**Its own state, never mixed with the legacy tree's.** aegis keeps its state in
`.aegis/state/` under the config root, where the legacy tree kept its own in
another format. `aegis serve` refuses to start on a state directory holding the
legacy tree's marker files, and says to move it to `.aegis/legacy-state/`; mixing
the two would have each read files the other wrote. Of `.aegis.yaml`, aegis reads
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
file against the harnesses actually installed (`doctor.py`).

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
250 ms. Publishing them per line doubled the server's cost per line.

**A session's attention is decided in Python, from facts and the agent's own
reports.** Working, error and waiting are facts the server holds: the turn, the
fold's last failure, live monitors, background tasks, held messages, queue tasks
and child sessions that are working or waiting themselves, so a wait is
transitive and a change in a child's card re-derives its parent's. Whether a finished turn asked the person something, showed
them something to read, or just finished, only the agent knows, and it says so
with `turn_end`; its plan comes from `plan_update`. Both are aegis records in the
store, so the fold derives a session's `standing` and a refold gives the same
card; the meta keeps it, so boot still reads no store. `attention.py` holds the
precedence. No model reads a transcript to guess what a turn meant.

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
enough, runs one `claude -p` on the agent named by `recap:` in `.aegis.yaml` (no
tools, no settings, no MCP, an empty working directory, thinking off), and appends
the answer as a `recap` record, so every browser receives the same entry. Nothing
in it is lazy on the wire, and the send that folds it gives it that send's `rev`,
so a tab returning with an older revision gets the folded row. One call
runs per session at a time and a point in the transcript is never paid for twice
unless a person forces it; the call kills its process when cancelled. Nothing in a
recap is sent to the agent.

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

**A harness is a module behind one interface.** `Session` asks a `Process`
(`harness.py`) to start, send, interrupt, set and end, and the fold reads each
stored line through the parser its `src` tag names. Claude Code
(`claude/harness.py`) speaks stream-json on stdio; OpenCode
(`opencode/process.py`) is one `opencode serve` per session over HTTP, because its
MCP token, like Claude's, is per process. Both parsers emit the same events, so
the entry rules exist once. OpenCode's token deltas are the one thing folded and
never stored: the part's closing update carries the whole text, so the live view
is ahead of a fresh fold only while a part is open (a prompt read mid-turn while
a part streams is the one known exception: live it sits after that part, and a
reload puts it before).

**A `/` line is resolved on the server, and a typo costs nothing.** `session.send`
runs an aegis command (`commands.py`) as the operation it stands for, passes a name
in the session's catalog to its harness as typed (OpenCode's run through its
command endpoint), and refuses anything else: Claude answers an unknown command
with the model. The catalog is the harness's own (Claude's `initialize` answer,
OpenCode's `/command` and `/config/providers`), in memory by harness and cwd,
never on disk, so an upgraded CLI never
meets a stale list. `/model`, `/effort` and `/permission` are aegis's, because the
spec the next `--resume` is built from has to change with the process; they reach
a live `claude` as control requests whose answers `ClaudeProcess.request` routes
and does not store.

**The inbox holds messages until a turn ends.** A monitor's wake, a queue's result
and a handoff reach a session as a user turn headed `> from <kind>:<name> · …`. An
idle session gets it at once and a stopped one is resumed for it; a working one
holds it and gets every held message as one turn when its turn ends. Writing to a
working `claude` would inject the message at its next tool boundary instead.

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
queue worker is finished only when its turn has ended with no live monitor, no
held message and no Claude task still open (`task_started` without its
`task_notification`). Reading the turn boundary alone as done closed a worker
mid-wait in the legacy tree.

**Agents change only what they created.** An agent reads and messages any session
it can see, and changes only its own monitors, its own session's names and the
tasks it enqueued. A session an agent spawns runs with at most the agent's own
permission, so spawning is never a way to gain power. People can do anything.
An agent never spawns, enqueues or reads on another server: across a link it
only hands off, to `handle@server` (`not_across_links` otherwise).

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

**The client knows no subsystem by name.** Server state reaches the browser as
named channels: a snapshot on subscribe, then numbered patches. A gap in the
numbers, or a reconnect, means resubscribe, saying which revision it holds; a
channel that keeps revisions answers with what changed after it, and any other
with a fresh snapshot. Adding a subsystem adds operations and channels, never a protocol field.

**Python decides, the browser draws.** Every fact and decision about a transcript
entry is computed once in Python: its glyph, title, summary, status, the diff
window of an edit, and what collapses. The entry crosses the wire as data and the
browser only turns it into markup. One copy of each fact means no drift, and a
data protocol version fails loudly across a link where mismatched markup would
break silently.

**The wire carries no collapsed detail.** A tool's arguments, output and diff, a
system note's tail and thinking text stay on the server until a row opens and
asks for them with `transcript.detail` (`transcript/wire.py`). They were 75% of a
snapshot's bytes over 80 real transcripts. Every entry carries `rev`, the store
index of the record that last changed it, so a returning client asks for what
changed since the revision it holds, and the client keeps the last 8 tabs it
showed.

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

**A key is one row in one table.** `client/js/keys.js` holds every key the client
answers and the one `keydown` listener that dispatches from it; the `?` list is drawn
from the same table, so it cannot name a key that does nothing. Chrome keeps Ctrl+T,
W, N and Tab, and on Linux Alt+1…9 and Alt+←/→, for itself, so the chords are the
Alt keys it leaves free. Plain keys act only outside a text field and the view
decides what they do: there is no mode. A selection, in the transcript or the
Fleet, is held by id and re-marked after every redraw, because both replace their
nodes on each patch. Esc closes, in order, a dialog, the drawer, the ? list and a
monitor card, and only then interrupts.

**A theme is one CSS file over one markup.** The markup carries everything any
theme might show, and the base stylesheet reads only CSS variables. A theme sets
the variables and a few overrides that decide what shows.

**One page for every screen.** A phone gets the desktop's markup. Below 760 px
one CSS block wraps the tab bar onto its own row, turns the side panel into a
drawer (`data-side=open`, opened by ☰) and stacks the Fleet band; under
`(pointer: coarse)` touch targets grow to 44 px and Enter in the composer adds a
line. A second set of screens would be a second client to keep in step. The
client asks through its own dialog (`js/dialog.js`), never the browser's:
`tests/test_client_rules.py` fails on `confirm(`, `alert(` or `prompt(`.

**The store keeps raw lines; entries are derived.** A transcript file holds the
harness's raw stdout lines and what aegis did (spawn, send, interrupt, exit,
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
system entry.

**Agent text is untrusted.** Markdown renders with raw HTML disabled, and the
localhost port is not trusted either: the websocket needs the server's token and
its own origin, because any page in the browser can open a socket to localhost.
The browser holds the token only as an HttpOnly cookie, named per state root,
that no script on the page can read.

**A sent file's URL is its key, and its content runs nowhere near the token.**
`file_send` copies the file into the state root under 128 random bits, and
`/files/<id>/<name>` serves it to anyone with the link, because `<img>`, a new tab
and a download cannot carry the token the websocket needs. HTML, SVG and XML are
served with `Content-Security-Policy: sandbox` (HTML with `allow-scripts`, so a
report still runs) and framed with `sandbox`: on aegis's own origin their script
could open the websocket, which the browser signs in with its cookie, and drive
every agent; the sandbox's opaque origin fails the socket's origin check
(`files.py`).
Open natively runs the desktop's opener on the server, so it is a person's
operation only, and only for a socket on loopback to a server with a desktop: a
proxy's public name is a browser elsewhere, and an SSH tunnel to a headless box
looks local but has nowhere to open the file.

**Audio never leaves the browser.** Dictation transcribes in the page, with
Cactus Whistle's WebAssembly build in two workers (`js/dictation.js`). The server
keeps the pinned engine and model, downloaded once from Hugging Face and checked
against their sha256, serves them at `/dictation/<pin>/` with an immutable cache
header, and answers `dictation.prepare` with that base and the keywords that bias
the model toward names it knows (`dictation.py`). Nothing carries samples, so
dictation works through any proxy that serves the page, and the server pays
nothing per word.

**Performance is measured on every PR and never gates.** `scripts/bench.py`
replays a recorded transcript through the fake harness in
`tests/fake_claude.py`, and CI compares the PR's base and head on the same
runner. A regression is a warning a reader has to weigh.
