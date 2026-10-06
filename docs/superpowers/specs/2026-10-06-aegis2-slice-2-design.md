# aegis2 slice 2: many sessions, shared tabs, lazy resume, archive

**Status: implemented, 2026-10-06** (issue #127), following
`docs/superpowers/plans/2026-10-06-aegis2-slice-2.md`. Where the build changed the
design, this file says so in place. Designed with Alex in one
brainstorming session; builds on slice 1
(`2026-10-06-aegis2-slice-1-design.md`) and the vision
(`2026-10-05-aegis2-vision-design.md`).

## What slice 2 delivers

Many Claude Code sessions on one server. The tab bar shows the server's open
sessions, the same in every browser, in an order each browser keeps for itself.
The Fleet view is home: one card per open session and, below it, the archive. A
server restart brings every open session back stopped, and the next prompt to
one resumes it. Sessions can be stopped, closed to the archive, reopened and
renamed.

Still out: the MCP endpoint (the next slice, moved up because Alex's agents rely
on `aegis_monitor`, `aegis_handoff` and `aegis_read_peer`), users, links,
plugins, panels, other harnesses.

## Decisions taken in the brainstorm

| Question | Decision | Why |
|---|---|---|
| What a restart does | Lazy: sessions come back stopped; `claude --resume` starts on the next prompt | Ten sessions would be ten processes at boot, about 4 GB, and the VPS has 3.7 GB; a forgotten session costs nothing |
| What the tabs are | Exactly the server's open sessions, shared by every browser | Nothing accumulates anywhere but on the server, where Close removes it for everyone |
| Tab order | Private to each browser, by drag, in browser storage | The set is shared; the arrangement is personal |
| What Close does | Archives: no process, no tab, listed in the archive | Accumulation is controlled on the server |
| The archive | A list with Reopen, replacing today's Ctrl+R history | Reopening an old conversation is used today, and it costs nothing with lazy resume |
| Idle reaping | None | A session waiting on its own background task wakes itself when the task ends; stopping it kills the task |

## Claude Code behaviour this relies on

Measured on zion, 2026-10-06, Claude Code 2.1.283, Haiku (issue #127):

- `claude -p --resume <session_id>` in stream-json mode keeps the session id,
  emits a fresh `init`, then only the new echo. Nothing from the earlier
  conversation is printed again, and the model remembers it. A resumed session
  appends to the same store file and the same fold continues.
- `total_cost_usd` is cumulative across `--resume` (0.0338 before, 0.0366 after
  a turn that cost about 0.0027), so the per-turn cost the fold derives stays
  right without a reset.
- A session that started a background Bash task ended its turn and woke itself
  30 s later with no prompt.

## The session lifecycle

| State | Process | Tab | Gets there by |
|---|---|---|---|
| live (`idle`, `working`, `error`) | running | yes | spawn, or a prompt to a stopped session |
| `stopped` | none | yes | a server restart, Stop, or the child exiting |
| `archived` | none | no; listed in the archive | Close |

- **Lazy resume.** `session.send` on a stopped session starts
  `claude --resume <claude session id>` in the session's cwd, records a
  `resume` record in the store, then writes the prompt. A session that never got
  a Claude session id (it died before `init`) resumes as a fresh process in the
  same store.
- **Stop** ends the process and keeps the tab: the way out of a wedged `claude`.
- **Close** stops the session if it is live, marks it archived, and removes it
  from the `sessions` channel.
- **Reopen** marks it open again; it comes back stopped.
- **No idle reaping.** Only shutdown, Stop and Close end a process.
- **A restart during a turn.** On boot, a session whose last status was
  `working` gets a system entry: "the server stopped during a turn".
- **A prompt never read** when its process ends (stop, exit, a server stop)
  shows as `lost`, not pending forever. (Added during the build.)
- **`error` is live.** A child that exited moves the session to `stopped` with
  the exit entry kept in the transcript; the next prompt resumes. (Slice 1 kept
  a dead session in `error` until closed; with resume there is no reason to.)
  `error` remains for an interrupt that went unanswered.

## Storage

- `<state>/transcripts/<log_id>.jsonl`, the store, unchanged.
- `<state>/sessions/<log_id>.json`, the meta: `log_id`, `handle`, `title`,
  `profile`, `model`, `effort`, `permission`, `cwd`, `claude_session_id`,
  `archived`, `created_at`, `last_activity`, `last_status`, `cost_usd`,
  `context_tokens`, `context_window`. Written by write-then-rename whenever one
  of these changes, at most once per second per session during a turn, and
  always at a turn's end, on stop, close and shutdown.
- **Boot reads only the meta files.** A missing or damaged meta file is rebuilt
  from its store: the spawn record gives the spec, the first `init` the Claude
  session id, the first send the title; it is marked archived. A store whose
  meta is rebuilt this way never takes the boot down.
- **The meta also keeps `activity`,** so the Fleet view needs no fold of any
  session at boot. (Added during the build.)
- **A store continues its numbering** when reopened, read from its last intact
  record, and starts a fresh line if a crash cut the last one. It opens its file
  only on the first write, so archived sessions hold no file open. (Added during
  the build.)
- **Folds are lazy.** A transcript is folded from its store on the first
  subscribe, then kept in memory while the session is open; an archived
  session's fold is dropped when its last subscriber leaves.

## Names

- **Handle:** generated at spawn from an adjective and a scientist's surname
  (`quiet-owl` style), unique among every session on the server, archived ones
  included. It is a label and never a key. `session.rename` accepts 2 or 3
  lowercase alphanumeric segments joined by hyphens, starting with a letter,
  and refuses one already in use.
- **Title:** defaults to the first line of the first prompt, cut at 60
  characters, with no model call. Free text up to 120 characters.

## The protocol

Protocol version stays `1`: slice 2's client and server ship together, and
nothing outside this repo speaks it yet.

| Operation | Params | Result | Errors |
|---|---|---|---|
| `profiles.list` | none | as in slice 1 | |
| `session.spawn` | as in slice 1 | `log_id`, `handle` | as in slice 1, minus `session_live` |
| `session.send` | `log_id`, `text` | none | `no_session`, `archived`, `claude_not_found` |
| `session.interrupt` | `log_id` | none | `no_session` |
| `session.stop` | `log_id` | none | `no_session` |
| `session.close` | `log_id` | none | `no_session` |
| `session.reopen` | `log_id` | none | `no_session`, `not_archived` |
| `session.rename` | `log_id`, `handle?`, `title?` | the new meta | `no_session`, `bad_handle`, `handle_taken`, `bad_title` |
| `archive.list` | `query?`, `limit` (default 50, max 200), `before?` (a `last_activity` cursor) | metas, newest first | |

`no_session` now means no session with that log id exists at all. An operation
on an archived session other than `reopen`, `rename` and reading its transcript
replies `archived`.

Channels:

- **`sessions`** replaces `session`. Snapshot: the metas of every open session,
  ordered by `created_at`. Patches: `{"upsert": meta}` and `{"remove": log_id}`.
  A change to status, handle or title publishes at once; activity, context and
  cost are coalesced for 250 ms. (Added during the build: publishing per changed
  field doubled the server's cost per line in bench2.)
  The meta on the wire adds `state` (`idle`, `working`, `error`, `stopped`) and
  `activity`, a one-line "what it is doing" computed in Python from the fold:
  the running tool's title and label, or the first line of the latest prose,
  cut at 80 characters.
- **`transcript:<log_id>`** works for any session, archived included.

## The client

- **Tab bar:** Fleet, then one tab per open session (status dot, handle, title
  cut to fit), then `+`. A tab can be dragged; the order is
  `localStorage["aegis2.tabs"]`, a list of log ids. A session not in the list
  goes at the end; ids that are no longer open are dropped. Which tab is
  focused is `#s=<log_id>` in the URL, so it is private to each browser and
  survives a reload. Alt+1 to Alt+9 focus a tab, Alt+0 Fleet.
- **Fleet:** one card per open session in tab order: status dot, handle, title,
  profile and model, the last two segments of the cwd, `activity`, time since
  `last_activity`, cost, a context bar. A working session's card has the accent
  border, an `error` one the error border, a stopped one is dimmed. Clicking a
  card focuses its tab. Below: the archive, with a filter box and the newest 50,
  each with Reopen and Read (a read-only transcript view with a Reopen button).
- **`+`** opens the spawn form as a view; spawning focuses the new tab.
- **Session view:** slice 1's, with Stop and Close in the sidebar, and the
  handle and title editable in place. A stopped session's composer reads
  "stopped; your next message resumes it".
- **Drafts** stay per log id. A tab whose session is closed elsewhere
  disappears; its draft stays in storage until the session is reopened.

## Server shape

- `registry.py` (new): the open sessions by log id, handle minting and the
  uniqueness rule, boot from the meta files, the archive list.
- `meta.py` (new): read, write (atomic, throttled) and rebuild meta files.
- `names.py` (new): the word lists and the generator. Copied in spirit from the
  old tree's handle pool, with no import.
- `session.py`: a session exists without a process. `ensure_running()` starts
  `claude` (with `--resume` when it has a Claude session id) on the first send;
  `stop()` ends it and keeps the session; `meta()` carries the new fields.
- `app.py`: the operations above; `sessions` channel.
- `transcript/entries.py`: `resume` and `server_stopped` records; `activity()`.

## Testing

Lesson tests first:

- booting over existing stores writes nothing to them and publishes nothing but
  snapshots (replay does not re-record);
- a session stopped and resumed continues the same store and fold, with no
  duplicated entries, and its patches still add up to its entries;
- a missing meta file and a damaged one are both rebuilt from the store, and a
  store too damaged to rebuild from is skipped with a log line, never fatal;
- handles never repeat, across archived sessions and across a restart.

The fake claude learns `--resume`: it keeps each session id's prompts in a file
under `$FAKE_CLAUDE_HOME`, and a prompt `/recall` answers with the earlier
prompts, so a test proves the resumed process has its context.

Browser tests add: two sessions in two tabs; drag to reorder, kept across a
reload; a server restart with the tabs back stopped and the next prompt
resuming; Close in one browser removing the tab from another; Reopen from the
archive; rename.

The bench adds boot time with 100 sessions on disk and the cold load of the
Fleet view with 20 open sessions. First run on zion, 2026-10-06: a registry boot
over 20 open and 80 archived sessions takes 7.3 ms; the Fleet view shows 20
cards 112 ms after navigation. The cost per Claude line rose from 69 to 82 µs at
p50 and from 163 to 233 µs at p95 against `main`, measured back to back; most of
the p95 is the meta file written at each turn's end (a `result` line costs 416 µs
against 235), which this design requires so a crash loses nothing.

## Done means

1. `make check`, browser tests and the bench pass.
2. Alex runs several sessions at once on zion, restarts the server, and resumes
   them by prompting; closes one, finds it in the archive, reopens it.
3. DESIGN.md's aegis2 part describes the lifecycle, the meta files and the
   shared tabs.
4. A changelog fragment.

## Out of scope

MCP tools; users; links; plugins; panels; OpenCode and lovelaice; a session
moving between servers; auto-titling with a model; deleting a session's files.
