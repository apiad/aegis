# aegis 2 slice 1: one Claude Code session in a browser tab

**Status: implemented, 2026-10-06,** on the branch `docs/aegis 2-slice-1`
(issue #124), following `docs/superpowers/plans/2026-10-06-aegis-2-slice-1.md`.
Designed with Alex in one brainstorming session. Where the build changed the
design, this file says so in place. It is the first slice of the aegis 2 vision
(`2026-10-05-aegis-2-vision-design.md`, on the branch `docs/aegis 2-vision` until
that PR merges) and the first code under `src/aegis/`.

## What slice 1 delivers

One Claude Code session, in one browser tab, on one server, usable for real work
in one repo. Alex starts `aegis serve`, opens the URL it prints, picks a profile
and a cwd, and works with the agent: prompts, a streaming transcript with readable
tool rows and failures, rendered Markdown, a status line with cost and context,
interrupt, and close.

What it does not do yet: more than one session, resume after a server restart,
an MCP endpoint for the agent, permission prompts (the session runs in the
profile's fixed permission mode), users, links and plugins. Those are later
slices in the vision's order.

The bar is daily use, not a demo. The vision says Alex switches at daily use,
and seeing real sessions in the client is the only real test of the transcript
design.

## Decisions taken in the brainstorm

| Question | Decision | Why |
|---|---|---|
| Walking skeleton or usable | Usable for real work in one repo | The switch happens at daily use; a skeleton tests the pipe and not the design |
| Protocol shape | The real one from the start: an operation registry and state channels | An ad-hoc protocol would be rewritten in slice 2, and the vision's two rules against protocol rot need the registry to exist |
| Where an entry becomes HTML | Python decides, the browser draws | See below |
| Client technology | Plain ES modules and the DOM, no framework, no build | A renderer is a function that returns a Node, so a plugin's renderer needs no framework |
| Where spawn options come from | The `agents:` map of `.aegis.yaml`, read-only | Profiles are how Alex spawns today; overrides are per spawn and never persisted |
| Security on localhost | A token and an `Origin` check, even before users exist | Without them any web page Alex visits could prompt a Claude running with full permission |
| Themes | All three from the vision, as CSS, from day one | The markup is designed for them; a theme costs one CSS file |
| Performance | Measured from day one, reported, never gated | Regressions get noticed on the PR that causes them |

### Python decides, the browser draws

Python computes every fact and every decision about an entry: its glyph, its
title, the one-line summary of a tool call, its status, the diff window of an
edit, and which part of a tool's output shows and which collapses. That logic
exists today in `src/aegis/render_shared.py` (433 lines: `describe_tool`,
`diff_window`, `result_parts`) and is copied, not ported to JavaScript. The entry
reaches the browser as data plus the raw Markdown of any prose. The browser turns
it into markup with one small renderer per entry kind and a vendored markdown-it
with raw HTML disabled.

Rendering finished HTML on the server was the first proposal and was rejected for
three reasons that bite in later slices:

- **Version skew across a link.** In the home-server model, zion's client shows
  VPS transcripts. Markup from an older VPS server under CSS from zion breaks the
  page with no error. Data carries a protocol version that fails loudly.
- **Plugins drawing their own entries.** With server HTML a plugin would supply a
  Python render function called over the plugin socket for every entry. With
  client rendering it ships a JavaScript renderer that the browser hot-reloads,
  which is the reason aegis 2 moved to the web.
- **Interactivity wants data.** Opening a 2 MB Read result on demand, syntax
  highlighting, and a file path that opens the file viewer are awkward over
  finished HTML.

What was given up: the phone parses Markdown for a long transcript. The bench
measures cold load of a 2,000-entry transcript, so that cost is a number and not
a guess.

## How Claude Code behaves, measured

Probed on zion with Claude Code 2.1.283, `claude -p` in stream-json in and out
with `--replay-user-messages`. A first prompt ran `sleep 6`; a second prompt was
written to stdin 2 s later, while the first turn was running.

| Time | Event |
|---|---|
| 0.5 s | `system hook_started`, `hook_response` |
| 0.7 s | `system init` |
| 2.0 s | second prompt written to stdin |
| 2.0 to 7.8 s | `system thinking_tokens`, six of them |
| 7.8 s | echo of the first prompt, then thinking, then the Bash call |
| 14.3 s | the Bash result, then the echo of the second prompt |
| 15.7 s | the answer to the second prompt only |
| 15.8 s | one `result` for both prompts |

Three rules follow, and each becomes a test:

1. **A turn is not one prompt.** A prompt sent mid-turn is injected at the next
   tool boundary, and one `result` closes both. Nothing may count turns by
   counting sends.
2. **The echo creates the user entry, never the send.** A sent prompt shows as
   pending until Claude echoes it. The echo is the moment Claude read it, so the
   transcript order is the order the model saw things in. Echoes match pending
   prompts in send order.
3. **System notices never start a turn.** `hook_started`, `init`,
   `thinking_tokens`, `task_started` and `task_notification` arrive outside turns
   and inside them. Only a sent prompt or a turn-bearing event (assistant text,
   thinking, a tool call, a tool result, a plan) moves the session to `working`;
   only `result` or the end of the stream moves it out. This is the lesson of
   `tests/test_claude_idle_promotion.py` in the old tree.

The composer stays enabled while a turn runs. Sending mid-turn steers the agent,
and the pending entry shows that it has not been read yet.

## The shape

`aegis serve` is one foreground process. It serves the client, holds at most one
live Claude session, and runs that session's `claude -p` as a child. Closing the
browser leaves the session running; stopping the server ends it. The transcript
is written to disk from the first event, though nothing reads it back until
slice 2.

| Module under `src/aegis/` | What it does |
|---|---|
| `roots.py` | The config root, state root and harness cwd, passed explicitly to everything that resolves a path |
| `profiles.py` | Reads the `agents:` map of `.aegis.yaml` under the config root |
| `claude/stream.py` | Parses stream-json lines into typed events; copied and adapted from `src/aegis/events.py` |
| `claude/process.py` | Spawns, writes to, interrupts and closes the `claude -p` child |
| `transcript/store.py` | Appends raw events to `<state>/transcripts/<log_id>.jsonl`; reads skip damaged lines |
| `transcript/describe.py` | Glyphs, labels, verdicts, args and diff windows of tool calls; copied from `render_shared.py` |
| `transcript/entries.py` | Folds store records into entries with every decision made |
| `session.py` | The session: status, cost, context, the fold, and publishing to channels |
| `ops.py` | The operation registry |
| `channels.py` | Named channels: snapshot on subscribe, then numbered patches |
| `web.py` | Starlette: static files and one websocket |
| `cli.py` | `aegis serve` |
| `client/` | Static ES modules, vendored markdown-it, three theme CSS files, fonts |

**The store keeps raw lines, not entries.** Each record is either a raw Claude
stdout line or something aegis 2 did (spawn, send, interrupt, exit, close), with
its receive time and its own index, so skipping a damaged line on load never
shifts the ids after it. Entries are derived by folding records on load, so a better
summary or glyph applies to old transcripts too, and slice 2's resume reads the
same file. A stdout line that does not parse is stored as an `unknown` event with
the raw text and renders as a system entry; it never stops the session.

**The session is a continuous fold.** The old driver iterated events per turn and
drained the aborted turn after an interrupt. Here one reader task folds every
stdout line as it arrives, whatever the turn state, so an interrupt only writes
the `control_request` and the resulting `result` flows through the same path.
The 16 MiB stdout line limit is copied with its reason: a single tool result line
can carry a whole file.

### One prompt, end to end

1. The browser calls `session.send` with the text.
2. The server writes the stream-json user message to Claude's stdin, sets the
   status to `working`, and publishes an `upsert` of a pending entry
   `pending:<n>`.
3. Claude echoes the prompt. The fold publishes `remove pending:<n>` and an
   `upsert` of the user entry.
4. Each later event is appended to the store, folded, and published as an
   `upsert` on `transcript:<log_id>`. A tool result upserts the entry its call
   created, keyed by `tool_use_id`, so the row flips from running to ok or err in
   place.
5. `result` sets cost, context use and `idle` on the `session` channel.

## The protocol

One websocket at `/ws` carries JSON messages. Static files are public; they hold
no secrets. The websocket must send `hello` with the token within 5 s, and its
`Origin` header must equal the server's own origin, or the server closes it.

| Direction | Message |
|---|---|
| client to server | `{"t":"hello","token":"…","proto":1}` |
| | `{"t":"call","id":7,"op":"session.send","params":{…}}` |
| | `{"t":"sub","channel":"transcript:<log_id>"}` and `{"t":"unsub",…}` |
| server to client | `{"t":"welcome","proto":1,"server":"zion"}` |
| | `{"t":"reply","id":7,"result":…}` or `{"t":"reply","id":7,"error":{"code":"…","message":"…"}}` |
| | `{"t":"snapshot","channel":"…","seq":0,"data":…}` |
| | `{"t":"patch","channel":"…","seq":n,"ops":[…]}` |

A `proto` mismatch is refused at `hello` with a message naming both versions.
`server` is the hostname until slice 4 gives servers canonical names.

**Sequence numbers.** Each channel numbers its patches per subscription,
starting after the snapshot's `seq`. A client that sees a gap resubscribes and
takes a fresh snapshot. A reconnect resubscribes everything. That one rule covers
dropped frames, a sleeping laptop and a server restart.

### Operations

Each operation is registered once with a name, a pydantic params model and a
handler. The websocket `call` is its first projection; the MCP projection arrives
when a slice needs it.

| Operation | Params | Result | Errors |
|---|---|---|---|
| `profiles.list` | none | profiles with name, harness, model, effort, permission, enabled | none |
| `session.spawn` | `profile`, optional `cwd`, `model`, `effort`, `permission` | `log_id` | `session_live`, `unknown_profile`, `harness_unsupported`, `bad_cwd`, `claude_not_found` |
| `session.send` | `log_id`, `text` | none | `no_session` |
| `session.interrupt` | `log_id` | none | `no_session`; idle is a no-op |
| `session.close` | `log_id` | none | `no_session` |

`session_live` means a session exists that has not been closed, including one in
`error`: a dead session stays on screen until someone closes it, and the spawn view
appears only when the `session` channel is null.

Params are validated by the model before the handler runs; a validation failure
replies `bad_params` with the pydantic message.

### Channels

- **`session`** holds the live session or `null`: `log_id`, `profile`, `model`,
  `effort`, `permission`, `cwd`, `status` (starting, idle, working, error,
  closed), `cost_usd`, `context_tokens`, `context_window`, `started_at`. Its patch
  ops are `{"set":{…}}`, a shallow merge of changed fields, and
  `{"replace":value}`, used when a session appears or goes away. The build
  added `replace`: a merge cannot turn an object back into null.
- **`transcript:<log_id>`** holds the entries in order. Its patch ops are
  `{"upsert":entry}` and `{"remove":id}`. An upsert of an unknown id appends; of a
  known id, replaces in place.

### Entries

```json
{"id":"toolu_01…","kind":"tool","status":"err","ts":1791290000.1,
 "glyph":"⌬","title":"Bash","summary":"mmdc -i d5.mmd -o d5.svg",
 "md":null,
 "detail":{"result":"exit 1","tail":"Error: Parse error on line 9…",
           "collapsed":false,"diff":null}}
```

- `kind`: `user`, `prose`, `thinking`, `tool`, `system`, `error`.
- `status`: `pending`, `running`, `ok`, `err`.
- `md`: the raw Markdown of user, prose and thinking entries; null otherwise.
- `detail`: per kind. For a tool: the one-line result, the tail of the output
  that shows, whether it starts collapsed, and the diff window of an edit as
  hunks of old and new lines.
- **Ids are deterministic,** so re-folding a store gives the same ids: a tool
  entry is its `tool_use_id`; every other entry is `e<record>.<block>`, since
  one Claude line can carry several blocks; a pending prompt is
  `pending:<record>`.
- **The context window** comes from the `result` line's
  `modelUsage[model].contextWindow`, which Claude Code 2.1.283 reports. The
  copied model table the design first planned was not needed.
- **A call still running when its turn ends** gets the verdict `interrupted`
  or `no result`, because no result will ever come for it.

## The client

Static ES modules under `client/`, served by the server, no build step.

- **One markup for three themes.** The transcript row markup and the theme
  variables are the ones in the vision spec's visual identity section; its HTML
  companion carries the working mockup to copy from. Ink, Logbook and Syalia are
  one CSS file each over a base stylesheet that reads only the variables. The
  theme choice lives in browser storage, picked from a menu in the top bar until
  the Settings view arrives.
- **Fonts** are vendored woff2 files under the SIL Open Font License, with the
  license texts beside them: Inter, Space Grotesk, JetBrains Mono, Source Serif 4.
  The sources are `repos/scriptorium` and `repos/syalia-ui`, the same files the
  mockup used.
- **Renderers.** `entries.js` maps each `kind` to a function
  `render(entry) → Node`. An upsert replaces the node with that id or appends a
  new one. Markdown goes through vendored markdown-it with `html: false`; links
  open in a new tab with `rel="noopener"`. A collapsed tool tail is a `<details>`.
- **Spawn view,** shown when the `session` channel is null: a profile picker from
  `profiles.list` (profiles on other harnesses listed and disabled), the cwd
  defaulting to the harness cwd, and model, effort and permission prefilled from
  the profile and editable for this spawn only.
- **Session view:** one tab for the session, the transcript, the composer, and a
  sidebar with the session (profile, model, cwd, status) and context (tokens
  against the window as a bar, cost).
- **Composer.** Enter sends, Shift+Enter is a newline, Esc or the Stop button
  interrupts while working. It stays enabled during a turn. The draft is kept in
  browser storage per `log_id`.
- **Scrolling** follows the bottom unless Alex has scrolled up; then a pill
  offers to jump to the latest entry.
- **Close** asks for confirmation, then returns to the spawn view.
- **Reconnect** with backoff from 0.5 s to 5 s, showing the disconnected state in
  the top bar.
- **Token.** The startup URL carries `?token=…`. The page moves it into
  `sessionStorage` and removes it from the address bar with `history.replaceState`.

## Errors

| What happens | What the user sees |
|---|---|
| `claude` exits or its stdout ends | Status `error`; a system entry with the exit code and the last 20 lines of stderr. The session stays on screen until closed. Stderr is also kept in `<state>/stderr/<log_id>.log` |
| A stdout line does not parse | An `unknown` event in the store and a system entry; the session goes on |
| A store line is damaged on load | The line is skipped and counted; one system entry says how many |
| Spawn fails | The reply's error code and message in the spawn view |
| An interrupt gets no `result` within 10 s | Status `error`, with a system entry saying the interrupt went unanswered |
| The websocket drops | The client reconnects and resubscribes |
| SIGINT or SIGTERM to the server | Claude gets SIGTERM, then SIGKILL after 5 s; the store is flushed |
| The port is taken | `aegis serve` exits with the port and the hint to pass `--port` |

## Roots, state, and the CLI

`aegis serve [--root PATH] [--port 8742] [--host 127.0.0.1]`. The CLI is the only
place that reads the process's working directory, once, to build the roots: the
config root is `--root` or the nearest ancestor holding `.aegis.yaml`; the state
root is `<config root>/.aegis/state`; the harness cwd defaults to the config
root. Nothing below the CLI calls `Path.cwd()`.

The log id is minted at spawn, before the child starts, and names the store file.
The handle of slice 1's session is cosmetic and is never a key.

The token is 32 random bytes, kept in `<state>/token` with mode 0600, and reused
across restarts so that an open tab survives a server restart. Deleting the file
rotates it. Binding to anything but loopback needs an explicit `--host`.

The old aegis uses `.aegis/state/` and its own ports, so both run on one machine.
`.aegis/` is added to the repo's `.gitignore` and to the Workspace's.

## Testing

**Lessons first.** These tests are written before the code they constrain:

- no file under `src/aegis/` imports `aegis` (AST, like
  `tests/webterm/test_imports.py`);
- no `Path.cwd()` under `src/aegis/` outside `cli.py` (AST, like
  `tests/test_no_cwd_regression.py`);
- the store file is named by a log id minted at spawn, never by a handle;
- a damaged store line is skipped and the rest of the transcript loads;
- a system notice never moves the session to `working`;
- the user entry comes from the echo, not the send, and two sends before one
  echo match in order;
- re-folding a store gives identical entries and ids;
- the patches a session published add up to its live entries. This one was
  added during the build, after a result's patch left out the update of an
  interrupted call while the live entries had it, so a reload and a live view
  disagreed.

**Unit tests** cover the stream parser (with its old tests copied), the fold, the
store, the registry's validation, channel sequence numbers, and the profile
reader.

**A fake `claude`.** `tests/fake_claude.py` speaks stream-json on stdin
and stdout. It replays a store file as a scripted session, echoes user messages,
and answers an interrupt `control_request` with an error `result`. Server
integration tests drive it through Starlette's websocket test client.

**Browser tests,** marked `browser` and run in CI, use Playwright and headless
Chromium against `aegis serve` with the fake: spawn from the form, send, watch
entries arrive, interrupt, reload the page and see the same transcript. Playwright
joins the dev dependency group.

**One live test,** marked `live`, runs real Claude Haiku through one prompt and
one interrupt. It is not run in CI.

## Performance

`make bench2` replays a recorded transcript through the fake `claude` at full
speed and reports:

- server cost per event, from a stdout line to the websocket frame, p50 and p95;
- browser latency per entry, from a stdout line until the entry is in the DOM,
  p50 and p95, in headless Chromium;
- cold load of a 2,000-entry transcript, from subscribe until the snapshot is
  painted;
- server RSS and the browser's JS heap after the replay.

The fixture is a store file, so any real session becomes a bench input. Slice 1
ships one recorded from a real working session, scrubbed of anything private.

First run on zion, 2026-10-06: 54 µs p50 and 135 µs p95 per Claude line on
the server; 2.1 ms p50 and 8.2 ms p95 from the fake writing a tool line to its
row being in the DOM; 762 ms cold load of 2,325 entries; 53 MB server RSS and
7 MB JS heap. Replaying to 2,000 rows took 12.6 s, about 3 ms per line. The cause is not
measured yet; the first suspect is the layout read in `Transcript.apply`, which
asks on every patch whether the reader is at the bottom.

**Noticed, never gated.** CI runs the bench twice on one runner, on the PR's
base commit and on its head, so the difference between runner machines cancels
out. It writes the table of deltas to the job summary and adds a `::warning::`
annotation for each metric more than 20% worse. The step never fails the run.
When the base has no aegis 2, as on slice 1's own PR, it reports the head's
numbers alone.

## Docs that change with this slice

- **AGENTS.md** says near the top that the old tree under `src/aegis/`, TUI
  included, is frozen and takes bug fixes only, and that new work goes to
  `src/aegis/` following the vision spec and the slice specs. "What done means"
  gains an aegis 2 version: `make check`, exercised in a browser against a server
  started after the change, the bench run, and DESIGN.md updated.
- **DESIGN.md** gains an aegis 2 part at the top with its process model and its
  cross-module rules, and the current content moves under a heading that marks it
  as the frozen tree's design. Every slice PR updates the aegis 2 part in the same
  change that alters the shape.

These two edits land with this spec. The DESIGN.md aegis 2 part describes the
design as specified, marked as such, until slice 1's code lands.

## Done means

1. `make check` passes, browser tests included.
2. Alex runs `aegis serve` on zion, spawns `opus` in a repo, and works with it:
   prompts, a mid-turn steer, an interrupt, a failing tool call, a page reload, a
   theme switch, and close.
3. `make bench2` runs locally and in CI, and the job summary shows its table.
4. DESIGN.md's aegis 2 part matches the code.
5. A `changelog.d/` fragment announces `aegis serve` as experimental.

## Out of scope

More than one session or tab; resume; the session list and the Fleet view; the
Settings view; users beyond the one token; links; the MCP endpoint and every
aegis tool; permission prompts; images or files in the composer; slash commands;
OpenCode and lovelaice; the file viewer; the command palette; microphone input.
