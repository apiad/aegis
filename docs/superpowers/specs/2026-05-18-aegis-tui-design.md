# Aegis TUI ("Phase 1.5") — Design

- **Date:** 2026-05-18
- **Status:** approved (pending written-spec review)
- **Builds on:** `2026-05-18-aegis-phase1-cli-design.md` (shipped)
- **Vision:** `vault/Atlas/Architecture/2026-05-17-aegis-vision.md` (this is the
  single-tab foundation of the vision's Phase 2 multi-tab TUI)

## Goal

Replace the line-based `aegis` REPL with a minimalistic full-screen Textual
TUI for a single live conversation, with tab-ready chrome and tab-state
signalling (colored status dot + bell on turn finish) built in from day one so
Phase-2 multi-tab inherits them with no rework.

## Locked decisions

1. **Single conversation, tab-ready chrome.** One live agent. The tab strip
   and status chrome render now (showing one tab); multi-agent plumbing
   deferred to Phase 2.
2. **Textual.** Built on Textual (rich's author; asyncio-native; stock
   scrollback/input widgets).
3. **Layout:** top tab strip → transcript → status line → input box.
4. **TUI replaces the line REPL.** `aegis` always launches the TUI;
   `src/aegis/repl.py` is removed. No `--plain` fallback.
5. **Tab-state signalling from day 1.** Per-tab colored dot (green ready /
   orange working / red error) + terminal bell on turn finish.

## Layout

```
┌ aegis ──────────────────────────────────────────┐
│ ● default                                        │  tab strip
├──────────────────────────────────────────────────┤
│ › explain this repo                              │
│ Aegis is a meta-harness…                         │  transcript
│ ⏺ Read(README.md)                                │  (RichLog, scrolls)
│   └ ok                                            │
├──────────────────────────────────────────────────┤
│ default · opus · auto          ✻ working…        │  status bar
├──────────────────────────────────────────────────┤
│ ▌type a message…                                 │  input
└──────────────────────────────────────────────────┘
```

## Architecture

Idiomatic Textual `App` with clean widget boundaries. A Textual worker
consumes the existing async `ClaudeSession` — its async iterator maps directly
onto Textual's worker model with no impedance mismatch.

### Module changes

| Path | Change |
|---|---|
| `src/aegis/tui/__init__.py` | **New.** Exports `AegisApp`. |
| `src/aegis/tui/app.py` | **New.** `AegisApp(App)` — composes the layout, owns the session worker, key bindings, state machine, bell. |
| `src/aegis/tui/widgets.py` | **New.** `TabStrip` and `StatusBar` widgets. Transcript = stock Textual `RichLog`; input = stock `Input`. |
| `src/aegis/tui/state.py` | **New.** `AgentState` enum (`ready`/`working`/`error`) — single source of truth for the status bar text and the tab dot. |
| `src/aegis/render.py` | **Refactor.** Replace the `Console`-coupled `Renderer` with a pure `render_event(ev) -> RenderableType | None` (None = not shown: `SystemInit`, `Unknown`). Shared event→renderable core; TUI writes it into `RichLog`. |
| `src/aegis/repl.py` | **Removed.** Session lifecycle (start → send → drain-until-`Result`) moves into the App worker. |
| `tests/test_repl.py` | **Removed/replaced** by `tests/test_tui.py`. |
| `src/aegis/cli.py` | `run` callback launches `AegisApp(session, profile).run()` instead of `asyncio.run(run_repl(...))`. Config / unknown-agent errors still handled before launch (unchanged). |
| `pyproject.toml` | Add `textual` to `dependencies`. |

### Unit boundaries

- `render_event(ev)` — pure: event in → renderable or None. No I/O. Unit-testable.
- `AgentState` — plain enum; transition logic lives in the App and is unit-testable in isolation.
- `TabStrip` — display-only. Given a list of `(name, AgentState)` it renders
  `<dot> <name>` per entry. Knows nothing about sessions. One entry in v1.
- `StatusBar` — display-only. Given `(agent, model, permission, AgentState)`
  renders the line. Knows nothing about sessions.
- `AegisApp` — the only unit that knows about the session/driver. Owns the
  worker, the state machine, key bindings, and the bell.
- `RichLog` / `Input` — stock Textual; no custom scrollback or line-editing code.

## Data flow (one turn)

1. `AegisApp.on_mount`: `await session.start()`, mount widgets, focus `Input`,
   state = `ready` (green dot, status `idle`).
2. User submits `Input` (Enter; empty/whitespace ignored).
3. App appends the user line to `RichLog` as `› <text>`, sets state =
   `working` (orange dot, status `✻ working…`), disables `Input`, starts a
   Textual worker.
4. Worker: `await session.send(text)`; then `async for ev in
   session.events()`: each event → `render_event(ev)`; non-None renderables
   written into `RichLog` **live as they stream**.
5. On `Result`: worker ends. State = `ready` (green), `Input` re-enabled and
   refocused, **`self.bell()`** fires (turn-finish ping).
6. Input is disabled for the duration of a turn — no interleaved turns.

## Tab-state signalling

Single `AgentState` enum drives two renderings that cannot disagree:

| State | Tab dot | Status-bar right | When |
|---|---|---|---|
| `ready` | 🟢 green | `idle` | mounted, or turn finished |
| `working` | 🟠 orange | `✻ working…` | turn in flight |
| `error` | 🔴 red | `⚠ <message>` | harness exited / turn errored |

- `error` clears to `working` on the next successful send (resend attempt),
  then to `ready`/`error` per outcome.
- A `Result` with `is_error=True` → `error` (red) + bell. A clean `Result` →
  `ready` (green) + bell.
- `TabStrip` holds a list keyed by agent name; Phase-2 multi-tab appends
  entries and the per-tab dot logic is unchanged.

### Ping

On every turn finish (`Result`, success or error) the App calls Textual's
`self.bell()` — terminal bell, zero dependencies, surfaces attention when the
terminal is unfocused (most terminals/OSes flag a belled window/tab). The bell
call is centralized in one method so Phase-2 can route it per-tab and swap the
transport. OS desktop notifications (`notify-send` / `terminal-notifier`) are
explicitly deferred (extra deps, platform-specific, unnecessary for the
single-terminal case).

## Key bindings

| Key | Action |
|---|---|
| `Enter` | Send the input (if non-empty and state is `ready`) |
| `Escape` | Interrupt the in-flight turn: cancel the worker, append a dim `^C` note to the transcript, state → `ready`, re-enable input. Best-effort (same posture as Phase-1). |
| `Ctrl+Q` | Quit (closes the session, exits cleanly) |
| `PageUp` / `PageDown`, mouse wheel | Scroll the transcript (free from `RichLog`) |

> Deviation from the original lock: interrupt is `Escape`, not `Ctrl+C`.
> Textual 8.x reserves `ctrl+c` (system `help_quit`; becomes copy when an
> Input is focused); rebinding it is brittle, so `Escape` (universal cancel)
> is used instead.

Thinking renders collapsed as `✻ Thinking…` — no expand toggle in v1.

## Error handling

- **Harness exits mid-session:** state → `error` (red, `⚠ harness exited`), a
  transcript note is appended, the app stays interactive; `Ctrl+Q` to leave.
  No crash traceback to the screen.
- **Spawn failure / bad argv:** surfaces at mount as a clean message; the app
  exits non-zero.
- **Config / unknown-agent errors:** handled in `cli.py` before the App
  launches (unchanged from Phase-1).
- **No TTY (piped/redirected):** Textual raises its standard "requires a
  terminal" error. Documented limitation — the line REPL that scripts could
  pipe into is intentionally gone (locked decision 4). The live `claude`
  integration test drives the *driver*, not the App, so CI is unaffected.

## Testing

- **`render_event`** — pure unit tests, porting the current `test_render.py`
  assertions: assistant text → Markdown; tool one-liner format; thinking
  collapsed (content not shown); tool_result first line only; error marker;
  `Result` separator; `SystemInit`/`Unknown` → `None`.
- **`AgentState` transitions** — unit tests: `ready→working→ready`,
  `working→error` on error `Result`, `error→working` on resend.
- **`AegisApp`** — Textual `App.run_test()` pilot harness with a `FakeSession`
  (the fake from the removed repl test, carried over): app mounts with tab
  strip + transcript + status bar + input present; submitting input calls
  `session.send` and appends the user line plus the rendered response to
  `RichLog`; state/tab-dot transitions ready→working→ready; `bell()` fires on
  `Result`; `Ctrl+Q` exits.
- **Driver / events / config tests** — unchanged.
- **Live `claude` smoke** — unchanged (driver-level, not via the App).
- All tests written and validated inline by the implementer — the
  verification layer is not delegated.

## Scope / non-goals (TUI v1)

Deferred: multi-tab plumbing, tab creation/switching, cross-tab focus,
thinking-expand toggle, copy/selection mode, UI themes/config, `--plain`
fallback, OS desktop notifications, per-tab bell routing. The chrome is
tab-*ready* (a list that renders one entry) but never shows more than one tab.

## Relationship to the vision

This is the single-tab foundation of the vision's **Phase 2** (multi-tab TUI +
cross-tab signalling). The `AgentState`/dot/bell primitives are deliberately
built tab-keyed so Phase 2 is "add tabs to the strip and a switcher", not a
rewrite.
