---
date: 2026-05-28
status: draft
type: design
topic: session-history
---

# Aegis — Session History (Ctrl+H)

A persistent, cross-process record of user-initiated agent sessions,
surfaced as a `Ctrl+H` modal. Closing a tab — or quitting and relaunching
aegis — should not erase the trail. Every prior session can be reopened
either fresh (new `AgentSession` with the same agent + cwd) or, where the
driver supports it, resumed with full conversation continuity.

## Motivation

The current `--resume` flow restores the most recent workspace at launch:
the tab roster from the last `workspace.json` write, with full transcript
replay from each tab's `sessions/<handle>.jsonl` event log. But:

- **Closing a tab erases its workspace.json row.** Its event log file is
  left behind, orphaned and metadata-less.
- **Relaunching with `--clean`, or after the workspace file has rolled
  past, makes earlier rosters invisible.**
- **There is no in-session way to look back.** You cannot see "what was I
  working on yesterday with the opus tab?" without grepping the state
  directory by hand.

This design closes that loop: a `Ctrl+H` modal that lists every recorded
session (open or closed, in this process or a previous one) and reopens
the selected row through the existing `drv.resume()` protocol.

## Scope

**In scope.** User-initiated agent sessions only:

- TUI tabs opened via `Ctrl+N` (the `AgentPicker`), `Ctrl+T` (default
  agent), or the implicit first tab at boot.
- Telegram-routed sessions created by `/new` or by bare-text routing
  from the bot.

**Out of scope.** Substrate ephemera:

- Queue workers spawned by `aegis_enqueue` — already observable via
  the `Ctrl+D` queue dashboard.
- Workflow-spawned agents from `engine.spawn()` — covered by workflow
  JSONL logs under `.aegis/state/workflows/`.
- Handoff targets — receivers are either user sessions (in scope) or
  workers (excluded).

Substrate ephemera will write the same per-session event log as today
(that mechanism is shared) but will **not** receive a "session opened"
meta header — and the Ctrl+H reader skips any log without a header.
That's the entire gating mechanism.

## Existing infrastructure (reused, not rebuilt)

The repo already implements most of what this feature needs. The design
deliberately routes through these:

- `aegis.state.workspace` — `WorkspaceTab` record (`handle, profile,
  order, provider, session_id, created_at`); `Workspace` snapshot;
  `save(state_dir, ws)`; `load(state_dir) -> Workspace | None`;
  `state_dir(cwd)`.
- `aegis.state.session_log` — `append_event(state_dir, handle, ev)`
  writes one JSON line per event under
  `.aegis/state/sessions/<handle>.jsonl`. `replay_events(state_dir,
  handle) -> EventReplay` reads it back.
- `aegis.tui.resume_plan` — `plan_resume(ws, agents, drivers) ->
  ResumePlan` classifies each `WorkspaceTab` as resumable or skipped
  (`profile_missing` / `driver_no_resume` / `no_session_id`).
- Driver `supports_resume: bool` + `resume(agent, cwd, mcp_url,
  handle, session_id) -> HarnessSession`. Claude implements both;
  Gemini and OpenCode are skipped automatically.
- `AegisApp._resume_agent_tabs(ws)` — the existing bootstrap path that
  iterates a `ResumePlan`, calls `drv.resume(...)`, replays events into
  fresh `ConversationPane`s, and rebinds the inbox router.

The Ctrl+H reopen path reuses **the same `drv.resume()` + `ConversationPane(replay=…)` mount** as boot-time resume. No new resume path.

## Architecture

Three small additions, zero behavioural changes to drivers or
`AgentSession`:

- **A meta header in the per-session event log.** A new
  `SessionMeta` event type, written as the very first line of every
  user-initiated `sessions/<handle>.jsonl`. Carries `handle, profile,
  provider, cwd, created_at, origin`. Substrate ephemera (queue
  workers, workflow spawns) skip this write.

- **A close marker in the per-session event log.** A new
  `SessionClosed` event, appended when the pane is closed (any reason).
  Carries `closed_at, reason ∈ {"user", "interrupt", "crash"}`. Missing
  marker at read time → inferred as `crash`.

- **A history reader.** `aegis.state.history.list_history(state_dir,
  *, live_handles) -> list[SessionHistoryRow]` globs
  `.aegis/state/sessions/*.jsonl`, reads only the first record of each
  file (the `SessionMeta`) plus a streamed scan for the last `Result`
  (preview text) and any `SessionClosed`. Skips files without a meta
  header. Cross-references `live_handles` to mark currently-open rows.

- **A modal.** `aegis.tui.history.HistoryModal` — Textual `ModalScreen`,
  shape mirroring `AgentPicker`. Reads via `list_history()`. Dismisses
  with one of: `("jump", handle)`, `("resume", row)`, `("open_fresh",
  row)`, or `None`.

- **A keybinding.** `Ctrl+H` → `AegisApp.action_open_history()`.
  Dispatches the modal's outcome to either the focus adapter
  (`jump`), the existing resume flow (`resume`), or a fresh
  `_spawn(slug, ..., cwd=row.cwd)` (`open_fresh`).

## Data shape

### `SessionMeta` event

A new variant in `aegis.events.Event` (sum type). Encoded by the existing
`event_codec.encode_event` / `decode_event` round-trip:

```python
@dataclass(frozen=True)
class SessionMeta:
    handle: str
    profile: str
    provider: str   # "claude-code" | "gemini" | "opencode"
    cwd: str
    created_at: str  # ISO-8601 UTC
    origin: str     # "tui" | "telegram"
```

Written exactly once, before the first turn of every user-initiated
session, via a new `session_log.append_meta(state_dir, meta)` helper that
guards the "must be first record" invariant.

### `SessionClosed` event

```python
@dataclass(frozen=True)
class SessionClosed:
    closed_at: str
    reason: str   # "user" | "interrupt" | "crash"
```

Appended on close through the existing `append_event` codepath. Reason is
selected by the call site:
- `"user"` — `Ctrl+W` close, `Ctrl+Q` quit, AppBridge `close(handle)`.
- `"interrupt"` — driver subprocess exit with non-zero rc.
- `"crash"` — not written explicitly; inferred at read time when the
  log has a `SessionMeta` but no `SessionClosed`.

### `SessionHistoryRow`

The reader's output type — derived, not persisted:

```python
@dataclass(frozen=True)
class SessionHistoryRow:
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    closed_at: str | None
    last_activity_at: str         # max(any aegis_ts in the log)
    preview: str                  # first ≤200 chars of the first user msg
    session_id: str | None        # latched from latest claude system:init
    is_open: bool                 # handle in live_handles
    crash_inferred: bool          # has meta but no SessionClosed
    profile_present: bool         # profile in current agents map
    driver_supports_resume: bool  # drivers[provider].supports_resume
```

`session_id` is read by the `list_history()` scan, NOT written explicitly.
The Claude driver already emits a `system:init` event carrying the
upstream session id; we just remember the last one we saw per file.

`preview` is derived from the first user-message event in the log. If
there is no user message yet, the field is empty.

## Reopen semantics

Each row in the modal has up to three actions, gated on row state:

- **Jump** (`Enter` when `is_open=True`). Switch the `ContentSwitcher`
  to the matching pane. No spawn.

- **Resume** (`Enter` or `r` when `is_open=False`,
  `profile_present=True`, `driver_supports_resume=True`,
  `session_id is not None`). Build a `WorkspaceTab(handle=…,
  profile=…, provider=…, session_id=…, …)` from the row and feed it
  through the existing resume codepath (`drv.resume(...)` →
  `ConversationPane(replay=…)`). The pane is mounted in the foreground
  and gets a `↻ resumed from history` banner.

- **Open fresh** (`Enter` or `f` when not resumable). Call
  `_spawn(profile, cwd=row.cwd)`. New handle. No history continuity.
  This is the fallback path for Gemini/OpenCode rows (where the driver
  refuses resume) and Claude rows where the upstream session id is gone.

When a row's `profile` is missing from the current `agents` map the row
is shown dimmed and both actions are disabled (matches the existing
`profile_missing` skip reason).

## UX (Ctrl+H modal)

`HistoryModal` styled after `AgentPicker` and `FilePickerModal`. Layout:

```
┌─ History ──────────────────────────────────────────────────────┐
│ /                                                              │  filter
├────────────────────────────────────────────────────────────────┤
│ ● lucid-knuth   claude-sonnet  2m ago   "fix the regression…" │
│ ↻ brave-turing  claude-opus    1h ago   "summarise this…"     │
│ ○ keen-codd     gemini-flash   3h ago   "let's refactor…"     │
│ ⊘ dim-hopper    <missing>     yesterday "…"                    │
└────────────────────────────────────────────────────────────────┘
 ↑↓ navigate · Enter primary · r resume · f fresh · / filter · Esc
```

Status glyphs:

- `●` = `is_open` — Enter jumps to the live tab.
- `↻` = closed, resumable (Claude + session_id present + profile + driver
  → Enter resumes; `f` forces fresh).
- `○` = closed, not resumable (Gemini/OpenCode, or Claude with no
  session_id) → Enter opens fresh.
- `⊘` = profile missing — non-actionable.

Sort: most recent `last_activity_at` first. Filter: substring match
against `handle + profile + cwd + preview`. The modal does not poll;
re-opening Ctrl+H re-reads (cheap — one disk scan, ≤ a few hundred
files).

The modal caps at the **500 most recent log files** on initial read.
Older logs remain on disk; a future "load more" affordance can lift the
cap without schema change.

## Wiring points

- `aegis.events` — add `SessionMeta` and `SessionClosed` to the `Event`
  sum type.
- `aegis.state.event_codec` — encode/decode for the two new variants.
- `aegis.state.session_log` — add `append_meta(state_dir, meta)`.
- `aegis.state.history` — new module: `list_history(state_dir, *,
  live_handles, limit=500) -> list[SessionHistoryRow]`.
- `aegis.tui.history` — new module: `HistoryModal`.
- `aegis.tui.app` —
  - bind `Ctrl+H` → `action_open_history()` (worker decorator,
    same shape as `action_pick_agent`).
  - emit `SessionMeta` from `_spawn(...)` for foreground-true /
    user-initiated paths. The `_SessionManagerAdapter.spawn()` path
    (queue workers) intentionally does NOT emit one.
  - emit `SessionClosed` from `_close_pane(...)` and `action_quit()`
    for `ConversationPane`s with a meta header.
- `aegis.telegram.frontend` — `/new` and bare-text routing call into
  the same `_spawn(...)` indirectly via `SessionManager.spawn(...)`.
  `SessionManager.spawn()` gains a parallel meta-header emission for
  the headless (`aegis serve`) path. Same shape, different surface.

## Configuration

No new `.aegis.yaml` section. The feature is always on for user-initiated
sessions; substrate ephemera always skip it. Retention / archival / per-
project disable are future extensions.

## Testing

`tests/test_session_meta_event.py` — codec round-trip for `SessionMeta`
and `SessionClosed`; backwards-compat assertion that a log without a
meta header still decodes its event tail.

`tests/test_history_reader.py`:

- Round-trip: write `SessionMeta` + a few events + `SessionClosed`,
  call `list_history()`, assert one row with the right shape.
- No meta header → file excluded from results.
- Meta present, no closed marker → row has `closed_at=None`,
  `crash_inferred=True`.
- `last_activity_at` derived from the highest `aegis_ts` in the log.
- `session_id` latches the **latest** `system:init` (not the first).
- Cap honoured: pass `limit=2`, assert the 3rd-oldest is dropped.
- `is_open=True` when the row's handle is in `live_handles`.

`tests/test_history_modal.py` (Textual snapshot harness):

- Empty state renders the empty-state placeholder.
- Populated state renders sorted-by-recency with correct glyphs.
- Filter narrows visible rows live.
- Dimmed row (profile missing) is non-actionable.

`tests/test_app_history_integration.py`:

- `_spawn(...)` writes a meta header on first call for the handle.
- `_close_pane(pane)` writes `SessionClosed` with `reason="user"`.
- `_SessionManagerAdapter.spawn(...)` (queue workers) writes NO
  meta header (worker JSONL is event-only, excluded from Ctrl+H).
- Pressing `Ctrl+H` then `Enter` on an open row activates the
  matching tab.
- Pressing `Ctrl+H` then `Enter` on a closed Claude row with
  `session_id` calls `drv.resume(...)` (assert via test double).

`tests/test_history_live.py` (marker `live`, requires `claude`):

- Open a session, send a turn, close, relaunch with `--clean`,
  press Ctrl+H, select the closed row, press Enter (resume) — verify
  the resumed session retains memory by asking a follow-up that
  references the earlier turn.

## Migration

None required. The substrate directory already exists. Existing
installations gain Ctrl+H rows starting at upgrade time; pre-upgrade
sessions that already have `sessions/<handle>.jsonl` event logs but no
meta header are excluded from the listing (showing them would force a
backfill of the missing metadata — out of scope).

## Future extensions (deferred)

- **Retention / archive policy.** Auto-archive logs older than N days
  into `.aegis/state/sessions/archive/`. YAGNI for v1; the 500-file
  initial cap absorbs the foreseeable growth.
- **Pre-upgrade backfill.** A one-shot tool that synthesises `SessionMeta`
  for orphaned logs by joining against the most recent `workspace.json`
  history. Useful only if pre-upgrade rosters turn out to be valuable.
- **Cross-host history.** Pulling the VPS substrate's session history
  into the laptop's Ctrl+H via the remotes substrate.
- **Full transcript preview pane.** The modal could optionally show a
  scrollable, syntax-coloured preview of the selected row's transcript.
- **Profile rebind UX.** Let the user re-bind a dimmed row to a new
  profile from inside the modal.
- **Pin / favourite.** Sticky rows at the top.
