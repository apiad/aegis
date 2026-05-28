---
date: 2026-05-28
status: draft
type: design
topic: session-history
---

# Aegis — Session History (Ctrl+H)

A persistent, cross-process record of user-initiated agent sessions, surfaced
as a `Ctrl+H` modal. Closing a tab — or quitting and relaunching aegis —
should not erase the trail. Every prior session can be reopened with the
same agent profile and driver, and (for Claude) optionally resumed with full
conversation continuity.

## Motivation

Aegis already persists every substrate-level artefact: queues, inboxes,
schedules, workflows, groups. Sessions themselves — the most user-visible
unit of work — are the one thing that disappears the moment a tab closes
or the process exits. There is no way to look back at "what was I working
on yesterday with the opus tab?", and no way to relaunch it with the same
profile + cwd without re-doing the Ctrl+N picker dance.

This design adds the missing surface: per-session JSONL persistence
matching the existing substrate idiom, plus a `Ctrl+H` modal that lists
prior sessions and reopens them.

## Scope

**In scope.** User-initiated sessions only:

- TUI tabs opened via `Ctrl+N` (the AgentPicker) or the implicit first tab.
- Telegram-routed sessions created by `/new` or by bare-text routing from
  the bot.

**Out of scope.** Substrate ephemera:

- Queue workers spawned by `aegis_enqueue` — already observable via the
  `Ctrl+D` queue dashboard.
- Workflow-spawned agents from `engine.spawn()` — covered by workflow JSONL
  logs under `.aegis/state/workflows/`.
- Handoff targets created indirectly by `aegis_handoff` — the inbox channel
  already records the message; the receiving session itself is either a
  user session (already in scope) or a worker (already excluded).

The gating mechanism is a single `record_history: bool` flag passed to
`SessionManager.spawn(...)`. Only the two user-initiated entry points pass
`True`.

## Architecture

Three new modules, no changes to drivers, events, or `AgentSession`:

- `src/aegis/core/history.py` — `SessionHistory` class. Owns per-session
  JSONL writes and the `list_sessions()` reader. Exposes
  `record_opened()`, `record_first_message()`, `record_claude_session_id()`,
  `record_closed()`. Append-only, fail-loud on read errors, atomic on
  write (line buffered, fsync on close).
- `src/aegis/tui/history.py` — `HistoryModal` (Textual `ModalScreen`).
  Mirrors `AgentPicker`'s shape. Reads via `SessionHistory.list_sessions()`,
  emits either `OpenFresh(record)` or `Resume(record)` messages back to
  the app.
- `src/aegis/tui/app.py` — adds a `Ctrl+H` binding to
  `action_open_history()`, handles the modal's outcome by delegating to
  `SessionManager` (existing spawn path).

`SessionManager` (`core/manager.py`) gains:

- A `_history: SessionHistory | None` field, set once at construction.
- `record_history: bool = False` parameter on `spawn(...)`. When `True`,
  the manager calls `_history.record_opened(...)` immediately after
  successful spawn and wires close-time `_history.record_closed(...)`
  through the existing close observer.
- A new `spawn_from_record(record, resume: bool)` convenience that
  resolves the record's agent profile from the current
  `AegisConfig.agents`, picks a fresh handle, and delegates to `spawn(...)`
  with the recorded `cwd` and (when `resume=True`) an extra
  `claude_resume_session_id` argument routed into the Claude driver.

The Claude driver (`drivers/claude.py`) gains one optional argv addition:
when `resume_session_id` is set on the `ClaudeCode` profile (or threaded
through at spawn time), `build_argv()` appends
`--resume <session_id>`. The stream-json protocol is unchanged.

## Persistence schema

One JSONL file per session:

```
.aegis/state/sessions/<ulid>.jsonl
```

ULID at the filename layer gives natural recency sort and is consistent
with how tasks are addressed elsewhere in the substrate. The directory
is gitignored along with the rest of `.aegis/state/`.

Event record types (each one line of JSON):

```json
{"event":"opened","ulid":"01J...","handle":"lucid-knuth","agent":"claude-sonnet","origin":"tui","cwd":"/home/apiad/Workspace/repos/aegis","ts":"2026-05-28T14:09:00Z"}
{"event":"first_user_message","preview":"first 200 chars of first user msg","ts":"2026-05-28T14:09:42Z"}
{"event":"claude_session_id","session_id":"abc-123-def","ts":"2026-05-28T14:09:43Z"}
{"event":"closed","reason":"user","ts":"2026-05-28T15:30:00Z"}
```

Field notes:

- `origin` ∈ `{"tui", "telegram"}`.
- `agent` is the profile name as it appears in `.aegis.yaml`. Resolving
  it back to a concrete `Agent` happens at reopen time against the
  *current* config, so renaming / removing a profile is observable
  (the row is shown dimmed and is non-actionable).
- `preview` is captured once, on first user message only. We do not
  store the full transcript in v1 — drivers and the runtime already
  log their own streams.
- `claude_session_id` is captured opportunistically. Claude Code's
  stream-json `system:init` event carries it; the existing event
  pipeline exposes it on `AgentSession`. If a non-Claude driver is in
  play, this record is simply never written.
- `closed.reason` ∈ `{"user", "interrupt", "crash"}`. A session that
  never wrote `closed` (process died) is treated as `crash` at next
  boot's `list_sessions()` read.

## Reading

`SessionHistory.list_sessions() -> list[SessionRecord]`:

- Globs `.aegis/state/sessions/*.jsonl`.
- For each file, folds the event stream into a single `SessionRecord`
  with: `ulid`, `handle`, `agent`, `origin`, `cwd`,
  `opened_at`, `closed_at | None`, `last_activity_at`, `preview`,
  `claude_session_id | None`, `is_open_in_process: bool` (computed by
  cross-referencing live `SessionManager` state at read time).
- Sorts most recent first by `last_activity_at`.
- Tolerates a torn trailing line (matches the convention already used
  by groups persistence).
- Caps at 500 most recent files for the modal's initial read; older
  files remain on disk and a future "load more" pagination can extend
  the cap without schema change.

## Resume semantics

The modal exposes two outcomes per row:

- **Open fresh** (`Enter`, default). Spawns a brand-new `AgentSession`
  with the recorded `Agent` profile and `cwd`. New handle, fresh
  generated name, no conversation continuity. Works for every driver.
- **Resume** (`r`). Claude only, only when the row carries a
  `claude_session_id`. Adds `--resume <session_id>` to the spawn argv.
  If the underlying Claude session is no longer in
  `~/.claude/projects/...` the driver fails loud and the modal shows
  the error inline (does not close).

Edge cases:

- **Profile renamed or removed.** Row is dimmed, marked
  `profile missing`, both actions disabled. A future "edit row" affordance
  could let the user re-bind to a new profile, but is out of v1 scope.
- **CWD no longer exists.** Spawn proceeds with the recorded cwd; the
  underlying driver surfaces the error on first turn — same path as if
  the user manually picked a stale cwd today.
- **Already-open session** (`is_open_in_process=True`). Default action
  becomes "jump to that tab" instead of "open fresh". Resume is
  disabled — there is nothing to resume against an already-live
  process.

## UX (Ctrl+H modal)

`HistoryModal` mirrors the visual idiom of `AgentPicker`. Layout sketch:

```
┌─ History ──────────────────────────────────────────────────────┐
│ /                                                              │  filter
├────────────────────────────────────────────────────────────────┤
│ ● lucid-knuth   claude-sonnet  2m ago   "fix the regression…" │
│ ○ brave-turing  gemini-flash   1h ago   "summarise this…"     │
│ ○ keen-codd     claude-opus    3h ago   "let's refactor…"     │
│ ⊘ dim-hopper    <missing>     yesterday "…"                    │
└────────────────────────────────────────────────────────────────┘
 ↑↓ navigate · Enter open · r resume · / filter · Esc close
```

Visual conventions:

- `●` = open in current process (Enter jumps to that tab).
- `○` = closed (Enter opens fresh; `r` resumes if Claude session_id
  present).
- `⊘` = unactionable (profile missing or other terminal-failure state).
- Columns: status glyph, handle, agent profile, relative-time of
  `last_activity_at`, preview snippet truncated to terminal width.

Keybindings:

- `↑` / `↓` — move cursor
- `Enter` — open fresh (or jump-to-tab if `●`)
- `r` — resume (Claude + session_id present)
- `/` — toggle filter input; substring match against
  `handle + agent + preview + cwd`
- `Esc` — close modal

The modal is read-only over `SessionHistory.list_sessions()` at open
time; we do not subscribe to live history events. Re-opening Ctrl+H
re-reads, which is cheap (≤500 small files) and consistent with how
the queue dashboard handles its own data.

## Configuration

No new top-level `.aegis.yaml` section in v1. The substrate is always
on for user-initiated sessions; substrate ephemera always skip it.

If a future need surfaces (retention policy, disable history per
project), we add a `history:` block then. YAGNI for v1.

## Telemetry / observability

None beyond the JSONL log itself. The log is the observability
surface: tailing `.aegis/state/sessions/*.jsonl` answers every
question we care about.

## Testing

`tests/test_history_persistence.py`:

- Round-trip: open → first message → close emits the expected event
  sequence; `list_sessions()` reads it back as one `SessionRecord`.
- Torn trailing line is tolerated.
- Missing `closed` event surfaces as `closed_at=None`,
  `crash_inferred=True`.
- Claude `session_id` capture is recorded when present, omitted
  otherwise.

`tests/test_history_modal.py`:

- Empty state (no files yet) renders the empty-state placeholder.
- Populated state renders sorted-by-recency.
- Filter narrows the visible rows live.
- Dimmed-row (profile missing) is non-actionable.

`tests/test_session_manager_history_integration.py`:

- `spawn(..., record_history=False)` produces no session file (queue
  workers, workflow spawns).
- `spawn(..., record_history=True)` produces exactly one file.
- Close path writes the closing event on every close reason (user,
  interrupt, crash via test harness signal).

`tests/test_history_live.py` (marker `live`, requires `claude`):

- Open a session, send a turn, close, reopen via `Resume` — verify
  the resumed session retains conversation memory by asking a
  follow-up that references the earlier turn.

## Migration

None. The substrate directory is created on first write. Existing
installations gain history starting at upgrade time; pre-upgrade
sessions are simply absent from `Ctrl+H`.

## Future extensions (deferred)

- **Full transcript persistence** — recording every `event` from the
  driver stream into the same JSONL file, so the modal could show a
  scrollable preview and `Ctrl+H` becomes a true "session browser".
  Requires deciding what to do with cost / token / tool-use records.
- **Cross-host history sync** — pulling the VPS substrate's session
  history into the laptop's Ctrl+H view via the remotes substrate.
- **Retention / archive** — auto-archive sessions older than N days
  into `.aegis/state/sessions/archive/`.
- **Profile rebind UX** — let the user re-bind a dimmed row to a new
  profile from inside the modal.
- **Pin / favourite** — sticky rows at the top of the modal.

None of these are needed for v1.
