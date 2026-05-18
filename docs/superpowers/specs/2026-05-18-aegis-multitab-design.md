# Aegis Multi-Tab (Phase 2) — Design

- **Date:** 2026-05-18
- **Status:** approved (pending written-spec review)
- **Builds on:** `2026-05-18-aegis-tui-design.md` + `2026-05-18-aegis-status-metrics-design.md` (shipped)
- **Vision:** `vault/Atlas/Architecture/2026-05-17-aegis-vision.md` — this is the
  vision's **Phase 2** (multi-tab TUI + cross-tab signalling). Handoff, MCP
  plane, queues remain later phases and are out of scope.

## Goal

Run many independent live agent conversations in one TUI: a one-line tab bar
listing every tab with its state dot and a uniquely generated handle, a single
visible conversation pane that switches instantly, per-tab agent profiles, and
cross-tab signalling (state dot + sticky attention mark + bell) so a tab that
finishes while you are elsewhere is obvious at a glance.

## Locked decisions

1. **Per-tab agent profile.** Each tab is an independent `HarnessSession` that
   may use a different config agent profile.
2. **Tab creation:** `Ctrl+T` spawns a tab on `default_agent` instantly;
   `Ctrl+N` opens an agent-profile picker modal.
3. **Layout:** one-line tab bar (all tabs, with dots) on top; one active
   conversation pane below (transcript + status + input); switching swaps the
   pane (no split-screen).
4. **Cross-tab signalling:** background tab finish/error → its dot reflects
   `AgentState` + a sticky `*` attention mark stays until the tab is focused +
   one terminal bell fires.
5. **Unique generated handle per tab:** `<adjective>-<turing-laureate>`
   (e.g. `lucid-knuth`); the agent profile slug is shown next to it in a
   distinct accent color.
6. **Architectural refactor:** extract a per-conversation `ConversationPane`
   unit; `AegisApp` becomes a shell that manages N panes.

## Naming resolved (vision open question, this layer)

User-facing = **tab**. Internal unit = **`ConversationPane`**. Live process =
**session** (`HarnessSession`). **Agent** = a config profile (slug like
`default`/`fast`/`opus`). **Handle** = the generated per-tab identity
(`lucid-knuth`).

## Tab bar & status bar display

Tab bar (single line; cannot stack, so slug sits *next to* the handle in an
accent color):

```
[●1 lucid-knuth ·default·] [●2 wry-hopper ·fast·]* [●3 brisk-dijkstra ·opus·]
  active highlighted          ↑ sticky * = finished while unfocused
```

- `●` is `AgentState.dot` (green ready / orange working / red error).
- Handle in normal weight; profile slug in a single CSS-driven accent color,
  consistent between tab bar and status bar so the eye links them.
- Active tab visually highlighted (reverse/bold). `*` appended when
  `pane.unseen` is true; cleared when the tab is focused.

Status bar (active pane, has room):

```
lucid-knuth  ·default·  opus · auto    ✻ working…    ↑1.2k ↓340 · ⚒ 7 · 12s / 4m03s
```

handle, accent slug, then the existing `model · permission` identity, state
label, and the metrics suffix (unchanged from the metrics increment).

## Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `tui/names.py` | `generate_name(taken: set[str], rng=random) -> str` → `<adj>-<laureate>`; regenerates on collision with `taken`. Pure, seedable. Module constants `ADJECTIVES`, `LAUREATES`. | — |
| `tui/pane.py` — `ConversationPane(Widget)` | One conversation. Owns `session`, `agent`, `agent_slug`, `handle`, `SessionMetrics`, `state: AgentState`, `unseen: bool`. Composes child `RichLog` + `StatusBar` + `Input`. Hosts the `_run_turn` worker, `interrupt()`, `close()`, `_finish`. Posts `PaneStateChanged` on every state transition. | `drivers.base`, `events`, `render`, `metrics`, `state`, `widgets` |
| `tui/widgets.py` — `TabBar` | Evolved from `TabStrip`. `set_tabs(items)` where each item = (index, handle, agent_slug, `AgentState`, unseen, active); renders the one-line bar with dots, accent slug, highlight, `*`. Display-only. `StatusBar` gains the handle + accent slug segment. | `state` |
| `tui/picker.py` — `AgentPicker(ModalScreen)` | Lists agent-profile names from the config `agents` map; Enter → returns the chosen slug (app spawns the tab), Esc → cancels. | `config` |
| `tui/app.py` — `AegisApp` | Shell: `panes: list[ConversationPane]`, active index, a `ContentSwitcher` holding the panes, the `TabBar`, global keymap, agent picker, cross-tab signalling, one shared 1s metrics tick for the active pane. Takes the config (`agents`, `default_agent`) + a session factory. | all above, `config` |
| `cli.py` | Build a session factory `make_session(agent: Agent) -> HarnessSession` from the resolved driver; pass `agents`, `default_agent`, factory to `AegisApp`. | `drivers`, `config`, `tui` |

`ConversationPane` is the unit the old mono-`AegisApp` becomes: the existing
`_run_turn`/`_finish`/`interrupt`/metrics/state logic moves there essentially
unchanged, parameterised by the pane's own session/agent/handle.

## Data flow & cross-tab signalling

1. App starts with one tab: `handle = generate_name(set())`, session via the
   factory for `default_agent`, `ConversationPane` mounted in the
   `ContentSwitcher`, `pane.start()`, active index 0.
2. Each pane's `_run_turn` worker streams events exactly as today (render,
   metrics, state) but scoped to that pane's widgets.
3. On every state transition a pane posts `PaneStateChanged(pane)` (a Textual
   `Message`) that bubbles to `AegisApp.on_pane_state_changed`, which:
   - rebuilds the `TabBar` from all panes;
   - if the change is a **turn finish/error** and `pane is not active_pane` →
     `pane.unseen = True` and `self.bell()` once;
   - active-pane finish → `self.bell()` (turn-finish ping, as today); no `*`.
4. Switch (`Ctrl+1..9`, `Ctrl+Tab`, `Ctrl+→/←`): set
   `ContentSwitcher.current`, `target.unseen = False`, focus its `Input`,
   rebuild `TabBar`.
5. One app-level `set_interval(1.0, self._tick)` refreshes only the active
   pane's `StatusBar` metrics. Background panes' `SessionMetrics` still
   accumulate from their own events; their clock is `now`-computed so it is
   correct whenever the tab is next viewed.

## Keymap (global, `AegisApp.BINDINGS`, priority)

| Key | Action |
|---|---|
| `Ctrl+T` | New tab, `default_agent`, instant |
| `Ctrl+N` | Open `AgentPicker`; selection spawns a tab with that profile |
| `Ctrl+W` | Close active tab (terminate its session); if it was the last tab, quit |
| `Ctrl+1`..`Ctrl+9` | Jump to tab N (no-op if absent) |
| `Ctrl+Tab` / `Ctrl+Right` | Next tab (wraps) |
| `Ctrl+Left` | Previous tab (wraps) |
| `Escape` | Interrupt the active pane's in-flight turn |
| `Ctrl+Q` | Quit: close all sessions, exit |

`Enter` (submit) stays on each pane's `Input`. `Escape`/interrupt acts only on
the active pane.

## Lifecycle

- **Spawn:** `generate_name` against the set of live handles → factory builds
  the session → `ConversationPane` mounted → `await pane.start()` → activated.
- **Background:** panes stay mounted in the `ContentSwitcher`; their workers
  keep running while hidden; events accumulate into their own `RichLog` and
  `SessionMetrics`.
- **Close (`Ctrl+W`):** `await pane.close()` (terminate that session),
  unmount, drop from `panes`, activate the nearest neighbor; closing the last
  tab calls `action_quit`.
- **Quit (`Ctrl+Q`):** close every pane's session, then `exit()`.

## Error handling

- Per-pane harness exit / exception: handled exactly as today but scoped to
  that pane (red `⚠` note in *its* transcript, its dot → red, its `unseen`
  set if it is not the active pane, one bell). Other tabs unaffected.
- Session spawn failure when opening a new tab: surface a red note in a
  transient way (the new pane mounts, shows `⚠ harness error`, dot red) rather
  than crashing the app; the tab can be closed with `Ctrl+W`.
- Agent picker with an unknown/empty selection: Esc/cancel is a no-op; an
  invalid slug cannot be chosen (list is built from the config map).
- `generate_name` exhaustion (more live tabs than adjective×laureate
  combinations is implausible — thousands of combos): if `taken` somehow
  covers all, fall back to appending a numeric suffix. Defensive only.

## Testing

- **`names.generate_name`:** seeded RNG → deterministic; format
  `^[a-z]+-[a-z]+$`; never returns a member of `taken` (collision path
  exercised by pre-filling `taken`); numeric-suffix fallback when the space is
  artificially exhausted (tiny stub lists).
- **`ConversationPane`** (pilot, `FakeSession`): submit → send + render +
  metrics + state; interrupt; `close()` calls `session.close`; posts
  `PaneStateChanged` on finish. Carries distinct `handle`/`agent_slug`.
- **`AegisApp` shell** (pilot, injected fake session factory): starts with one
  tab; `Ctrl+T` adds a tab with a unique handle; switching to a tab clears its
  `unseen`; a background pane finishing sets that pane's `unseen` and fires
  exactly one bell (capture `app.bell`); `Ctrl+W` closes the active tab and
  closing the last tab exits; `Ctrl+N` opens the picker and selecting a
  profile spawns a tab using it; `TabBar` text contains each tab's handle,
  accent slug, dot, and `*` only when unseen.
- **Existing single-tab pilot tests** are rewritten against the new shape
  (`ConversationPane` for conversation behavior; `AegisApp` for shell
  behavior). The refactor preserves behavior; the suite stays green at every
  commit. Test work is written/validated inline (not delegated). The live
  `claude` driver test is unchanged (driver-level).

## Non-goals

Split-screen / tiling, drag-reorder tabs, scrollback or metrics persistence
across `aegis` restarts, manual tab renaming, configurable keymap, the MCP
plane / live-handoff / sequential-handoff / task queues / distribution (all
later vision phases).
