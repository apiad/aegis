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

**A session outlives its process.** A session is live (a harness child, `claude`,
`opencode serve` or `codex app-server`, is running), stopped (none is), or archived (closed: no process,
no tab). A prompt to a stopped session starts the harness again on the same
conversation (`claude --resume`, `opencode serve` and the same session id, or
`thread/resume` on the same Codex thread),
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
precedence. No model reads a transcript to guess what a turn meant. The fold also
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
MCP token, like Claude's, is per process. Codex (`codex/process.py`) is one
`codex app-server` per session over JSON-RPC on stdio, for the same reason, and
the only harness whose model, effort and sandbox travel with every turn, so only
a new provider restarts it. Its process writes `aegis/*` lines of its own for what
only a response carries (the version, the thread, each turn's model), and kills
its process group after any exit, because a grandchild of a dead child held the
thread's writer lease and the child's pipes. The parsers emit the same events, so
the entry rules exist once. Token deltas, OpenCode's, Codex's and Claude's
(`--include-partial-messages`), are the one thing folded and never stored: the
part's closing update carries the whole text, so the live view is ahead of a
fresh fold only while a part is open. OpenCode's closing update keeps the part's
id; Claude's is the assistant line for that block, whose row replaces the one its
deltas drew (`claude/stream.py`). A prompt OpenCode reads mid-turn while a part
streams is the one known exception: live it sits after that part, and a reload
puts it before.

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

**aegis nudges an agent that drifts, once.** A live, idle session that is
not a queue worker, whose card says done or review, gets an inbox message
headed `> from aegis:nudge · plan|idle · …` when its plan has gone 15 minutes
of work untouched with items not done, or when its last turn ended with no
`turn_end` 10 minutes ago (`nudges.py`). What a nudge needs is folded into the
standing from the store (`ended`, `plan_mark`, `nudged`), so a restart neither
repeats one nor forgets it. A send that carries no nudge re-arms the idle
nudge and only a plan record re-arms the plan nudge, so a nudge's own turn
never earns another and nothing loops.

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
queue worker is finished only when its turn has ended with no live monitor, no
held message and no Claude task still open (`task_started` without its
`task_notification`). Reading the turn boundary alone as done closed a worker
mid-wait in the legacy tree.

**Agents change only what they created.** An agent reads and messages any session
it can see, and changes only its own monitors, its own session's names and the
tasks it enqueued. A session an agent spawns runs with at most the agent's own
permission, so spawning is never a way to gain power. People can do anything.
Closing is the one exception, and it asks for a second thought: `session_close`
closes at once only a session the agent spawned, or a worker whose task it
enqueued, whose attention is done. Anything else, the caller's own session
included, is refused with a prose account of that session (who started it, its
state, attention, plan step and last activity) and a one-time token bound to the
caller and the target; the same call with the token closes it, because a person
may be reading that tab (`confirm.py`).
Reading includes waiting: `monitor_sessions` watches other sessions' attention
cards and wakes its owner when all have finished or one is blocked on the
person, and `session_list` shows each card and plan. Neither changes the
sessions it watches.
A person's `/spawn` makes nobody's child, but its first message carries the
last turns of the tab it was typed in and that tab's handle, so the new agent
can find the referent of a three-word task (`agent_ops.spawn_opening`).
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

**Folding hides rows, it never unmounts them.** From which fold level a kind
folds is a fact on the entry (`fold`: 0 never, 1 the work, 2 also what arrived
and what was shown; `transcript/entries.py`). The client draws each run of
consecutive entries folded at the reader's level as one line on its first
mounted row and hides the rest, so the mounted rows stay a suffix of the
transcript and the window, the trim and the reader's place work unchanged; the
walks (j/k, the first row on screen) skip hidden rows. The line is counted from
the run's data, not its rows, so a run that starts above the mounted rows still
says all it holds.

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
the variables and a few overrides that decide what shows.

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
sheet over that drawer and stacks the Fleet band; under `(pointer: coarse)` touch
targets grow to 44 px and Enter in the composer adds a line. Hover opens and
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
operation for it is open to agents. Each harness is started with read access
to its inbox, and a chunk's bytes never reach the log (`attachments.py`).

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
inbox.

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
nothing per word.

**Performance is measured on every PR and never gates.** `scripts/bench.py`
replays a recorded transcript through the fake harness in
`tests/fake_claude.py`, and CI compares the PR's base and head on the same
runner. A regression is a warning a reader has to weigh.
