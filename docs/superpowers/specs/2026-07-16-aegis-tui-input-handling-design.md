# Aegis TUI input handling + handoff interrupt — design

**Date:** 2026-07-16
**Status:** design
**Scope:** five input-key behaviors in the TUI chat input, plus one MCP change
(`aegis_handoff` gains an `interrupt` flag).

## Motivation

The TUI input (`GrowingInput`, a Textual `TextArea`) currently supports only two
input gestures: `Enter` submits (enqueues via the inbox), and
`shift+enter`/`ctrl+j`/`alt+enter` insert a newline. `Esc` is an app-level
priority binding that interrupts the current turn unconditionally.

This is thin. There is no way to:

- clear a half-typed message without selecting-and-deleting;
- send a message that *interrupts* the agent's current turn instead of queuing
  behind it;
- recall and re-send a previous message.

And peer agents cannot interrupt each other: `aegis_handoff` always queues behind
the target's live turn, so an urgent correction waits until the target's next
turn boundary.

This design adds the missing gestures and the handoff-interrupt path.

## Key semantics

| Key | Behavior |
|-----|----------|
| **Enter** | Enqueue (unchanged — `deliver()`; lands on idle, chips mid-turn) |
| **Shift+Enter** / **Ctrl+J** | Newline |
| **Alt+Enter** (+ **Ctrl+Enter** where the terminal distinguishes it) | Send-with-interrupt |
| **Esc** — input non-empty | Clear the input box |
| **Esc** — input empty | Interrupt the current turn (today's behavior) |
| **Up / Down** | Boundary-aware history recall |

### Why Alt+Enter (not Ctrl+Enter) is the primary interrupt-send key

`alt+enter` sends `ESC CR` and Textual sees it as a distinct key across
essentially all terminals. `ctrl+enter` and `shift+enter` are only
distinguishable from plain `Enter` in terminals speaking the Kitty keyboard
protocol (kitty, foot, WezTerm, Ghostty, recent iTerm2); in legacy terminals
`ctrl+enter` collapses to `Enter`. So:

- **Alt+Enter** is the load-bearing interrupt-send key (portable).
- **Ctrl+Enter** is wired too, as a bonus alias for Kitty-protocol terminals.
  Where it is indistinguishable from `Enter` it simply never fires — harmless.
- **Newline** keeps `ctrl+j` (works everywhere) and `shift+enter` (Kitty
  nicety). Dropping `alt+enter` from the newline fallback list costs nothing
  because `ctrl+j` always covers newline.

## Components

### 1. `GrowingInput` (`src/aegis/tui/widgets.py`)

Owns the key-level decisions in its `_on_key`:

- `enter` → `action_submit()` posting `Submitted(kind="enqueue")`.
- `alt+enter` (and `ctrl+enter`) → `action_submit()` posting
  `Submitted(kind="interrupt")`.
- `shift+enter` / `ctrl+j` → insert newline (unchanged mechanics).
- `up` / `down` → history recall, boundary-aware (below). When not at a
  boundary, fall through to `super()._on_key` so the cursor moves normally.

The `Submitted` message grows a `kind: str` field (`"enqueue"` | `"interrupt"`).
Default stays `"enqueue"` so existing call sites and tests are unaffected.

**Boundary detection.** "Cursor on the first visual line" and "cursor on the
last visual line" are computed from the `TextArea` cursor location against the
document. Up recalls a previous entry only when on the first line; Down recalls a
newer entry only when on the last line. This preserves normal multi-line cursor
movement and matches zsh/fish muscle memory.

**History ring.** Per-pane, in-memory, session-lifetime (not persisted — matches
how the pane holds transient UI state today). Both `enqueue` and `interrupt`
sends append the sent text to the ring. Design of the ring:

- A list of previously-sent strings plus a cursor index.
- On first Up from a fresh (un-recalled) state, the **current buffer is stashed**
  as the draft; the ring cursor moves to the newest entry.
- Further Up moves toward older entries; Down moves toward newer.
- Down past the newest entry **restores the stashed draft** and exits recall.
- Editing a recalled entry and sending it appends a *new* ring entry — it never
  mutates the recalled entry in place.
- Duplicate consecutive sends collapse to a single entry (optional nicety; keep
  only if trivial).

The ring lives on the widget (or the pane and is passed in) — one ring per
`GrowingInput` instance, so each tab has its own history. Placement decision:
keep the ring on the `GrowingInput` widget itself, since it is 1:1 with a pane
and the widget already owns the key handling.

### 2. Pane / App (`src/aegis/tui/pane.py`, `src/aegis/tui/app.py`)

**`on_growing_input_submitted`** branches on `event.kind`:

- `"enqueue"` → existing path: build `InboxMessage`, `deliver()`, chip if queued.
- `"interrupt"` → if the pane is `working`, interrupt the live turn first
  (`self.interrupt()` / `self._core.interrupt()`), then send the text as the next
  turn. If idle, behaves exactly like `"enqueue"` (nothing to interrupt).

**Esc clear-vs-interrupt.** Keep the single app-level `escape` priority binding.
`action_interrupt` (app.py) first asks the active pane's input whether it holds
text:

- non-empty → clear the input (`inp.value = ""`) and stop.
- empty → fall through to the existing interrupt behavior.

Modal dismiss still wins first (the existing screen-stack logic in
`action_interrupt` already only runs on the default screen). No new binding; one
branch added. The pane exposes a tiny helper (e.g. `clear_input_if_present() ->
bool`) so the app doesn't reach into widget internals.

### 3. `aegis_handoff(interrupt: bool = False)` (`src/aegis/mcp/server.py`)

New keyword param, default `False`:

- `interrupt=False` → today's behavior, byte-for-byte (queues mid-turn via
  `inbox_router.deliver`, lands on idle).
- `interrupt=True` → call the target session's interrupt primitive first, then
  `inbox_router.deliver`. Because interrupt drops the target to idle, the
  handoff lands immediately as the target's next turn instead of buffering.

**Bridge surface.** The MCP server needs an interrupt-by-handle entry point on
the `AppBridge` protocol. Add `async def interrupt(self, handle: str) -> None`
to `AppBridge` (`src/aegis/mcp/bridge.py`). `SessionManager.interrupt(handle)`
already exists (delegates to `AgentSession.interrupt`); `AegisApp` gains a
matching method that interrupts the named pane (not just the active one). The
underlying `AgentSession.interrupt()` is unchanged.

**Return string.** Distinguish the interrupt case from the existing land/queue
cases, e.g.:

- `interrupt=True`, target was working → `interrupted & landed at <target>`.
- `interrupt=True`, target was idle → `landed at <target>` (nothing to interrupt).
- `interrupt=False` → unchanged (`landed at …` / `queued for … (position N)`).

**Docstring / agent guidance.** Update the `aegis_handoff` docstring and the
BRIEFING/PRIMING text so agents know *when* to interrupt a peer: use
`interrupt=True` only when you have a blocking correction the peer needs *now*
(e.g. it is about to act on a wrong assumption), not at its next turn boundary —
interrupting discards the peer's in-progress turn, so it is a deliberate act, not
the default.

## Data flow

```
key press
  │
  ├─ Enter ─────────────► Submitted(kind="enqueue") ─► deliver() ─► land | chip
  ├─ Alt/Ctrl+Enter ────► Submitted(kind="interrupt") ─► if working: interrupt
  │                                                        then send now
  │                                                     else: deliver()
  ├─ Shift+Enter/Ctrl+J ► insert newline
  ├─ Esc ───────────────► app action_interrupt:
  │                          input non-empty → clear
  │                          input empty     → interrupt turn
  └─ Up/Down ───────────► boundary? → history recall (stash/restore draft)
                          else       → normal cursor move
```

```
aegis_handoff(from, target, context, interrupt)
  │
  ├─ interrupt=True  ─► bridge.interrupt(target) ─► inbox_router.deliver ─► lands now
  └─ interrupt=False ─► inbox_router.deliver ─────► lands on idle / queues mid-turn
```

## Error handling

- **Empty send:** Enter/Alt+Enter on whitespace-only input is a no-op (existing
  guard in `on_growing_input_submitted`).
- **Interrupt-send while idle:** no interrupt call; degrades to a normal send.
- **Ctrl+Enter indistinguishable from Enter:** the `ctrl+enter` branch never
  fires; `alt+enter` remains the working path. No error, no surprise.
- **History on empty ring:** Up/Down at a boundary with no history is a no-op
  (cursor stays; nothing recalled).
- **Handoff interrupt on unknown/self target:** existing rejection paths run
  before any interrupt is attempted (validate target first, then interrupt).

## Testing

Hermetic widget/unit tests (no subprocess):

- **Esc:** non-empty clears + does not interrupt; empty interrupts.
- **Alt+Enter / Ctrl+Enter:** posts `Submitted(kind="interrupt")`; idle path
  degrades to a plain send; working path interrupts then sends.
- **Enter:** posts `Submitted(kind="enqueue")` (regression guard).
- **Newline:** `ctrl+j` and `shift+enter` insert `\n`; `alt+enter` no longer
  inserts a newline.
- **History ring:** Up/Down cycle; boundary gating (up mid-buffer moves cursor,
  not history); draft stash on entry and restore on Down-past-newest; recalled +
  edited send appends a new entry rather than mutating.

MCP tests:

- `aegis_handoff(interrupt=True)` calls `bridge.interrupt(target)` before
  `inbox_router.deliver`; return-string variants for working vs idle vs
  `interrupt=False`.
- Live handoff-interrupt round-trip guarded by the `live` marker (needs
  `claude`), asserting the target's in-progress turn is cut and the handoff runs
  next.

## Out of scope

- Persisting input history across process restarts (session-lifetime only).
- A visible history-search UI (`Ctrl+R`-style). Up/Down cycling only.
- Changing the queue/inbox semantics of a normal (non-interrupt) send.
