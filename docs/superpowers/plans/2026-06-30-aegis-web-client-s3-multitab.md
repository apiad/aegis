# Aegis Web Client — S3 (Multi-tab + Agent Picker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Builds on S2. Wire contract: `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`.

**Goal:** Turn the single-tab web client into a multi-tab one — a TabBar with a chip per session, an agent picker (Ctrl+N / "+") that spawns new tabs, click-to-switch, per-tab transcripts, cross-tab signaling (state dot, unseen marker, title pulse), tab close, and cross-window coherence (two browser windows on one `aegis serve` see the same tab list).

**Architecture:** The backend gains a *live* `session_list` global stream — the `SubscriptionRegistry` tracks global subscribers and re-broadcasts the full session list whenever a web client spawns/closes a session, so every window updates. The frontend is restructured around a `tabs` map (handle → per-tab state + DOM pane + chip); the `session_list` frame is the source of truth that drives tab creation/removal via a pure `reconcileTabs`. Event/state/inbox frames route by `handle` to the right tab; inactive tabs accrue an unseen marker and pulse the document title.

**Tech Stack:** Starlette (S2a), vanilla ES modules, node for the pure `reconcileTabs` test. No new deps.

## Global Constraints

- Build on S2; S2's tests stay green (`test_web_*`, node `coalesce`/`markdown`).
- No SPA / no build step. Plain ES modules.
- `session_list` frame shape becomes a **full snapshot**: `{kind:"session_list", sessions:[{handle, agent_slug, state, active, unseen}, ...]}` (replaces S2a's coarse `added/removed/updated`). The frontend diffs it. Update `supported_kinds` stays the same (`session_list`).
- Cross-window coherence covers **web-initiated** spawn/close (broadcast on those RPCs). Sessions created outside the web (TUI/MCP/queue) appear on the subscriber's next reconnect or next broadcast — documented, not silent.
- Commit straight to **main**. Conventional commits.

## File Structure

**Modified (backend):**
- `src/aegis/web/subscriptions.py` — global subscriber set + `subscribe_global`/`unsubscribe_global`/`session_list_frame`/`broadcast_session_list`.
- `src/aegis/web/wssession.py` — register a global sink on `subscribe(global)`; broadcast on `spawn_session`/`close_session`; unsubscribe on cleanup; switch the frame to the full-snapshot shape.

**New (frontend):**
- `src/aegis/web/static/js/tabs.js` — pure `reconcileTabs(existingHandles, sessions)`.
- `tests/web/tabs.test.mjs` — node test for it.

**Rewritten (frontend):**
- `src/aegis/web/static/js/app.js` — multi-tab controller.
- `src/aegis/web/static/index.html` — `#tabbar`, `#panes`, `#modal-root`.
- `src/aegis/web/static/css/base.css` — tabbar/chip/modal styles.

**Tests:**
- `tests/test_web_session_list.py` — backend broadcast.

---

### Task 1: Live `session_list` broadcast (backend)

**Files:** Modify `subscriptions.py`, `wssession.py`. Test: `tests/test_web_session_list.py`.

**Interfaces — Produces (`SubscriptionRegistry`):**
- `subscribe_global(sink: Sink) -> None`
- `unsubscribe_global(sink: Sink) -> None`
- `session_list_frame() -> dict` → `{"type":"stream","kind":"session_list","sessions":[asdict(si) for si in manager.list_sessions()]}`
- `broadcast_session_list() -> None` → pushes `session_list_frame()` to every global sink.

**WSSession changes:**
- `__init__`: `self._global_sink = lambda fr: self._emit(fr)`; `self._global_on = False`.
- `_subscribe` global session_list: `self._reg.subscribe_global(self._global_sink); self._global_on = True; self._emit(self._reg.session_list_frame())`.
- In `_call`, after a successful `spawn_session` and `close_session`: `self._reg.broadcast_session_list()`.
- cleanup (`finally` in `run`): if `self._global_on`, `self._reg.unsubscribe_global(self._global_sink)`.

**Tests:** two fake transports/WSSessions sharing one registry+manager; A subscribes global (gets initial snapshot); B spawns a session via rpc → A receives a `session_list` frame whose `sessions` includes the new handle. Close → A receives an updated frame without it. (Reuse the `FakeManager`/`FakeTransport` from `test_web_protocol.py` — its `spawn` already mutates `_cores`.)

TDD → commit.

---

### Task 2: `reconcileTabs` (pure, node-tested)

**Files:** Create `src/aegis/web/static/js/tabs.js`, `tests/web/tabs.test.mjs`.

**Interfaces — Produces:**
- `reconcileTabs(existingHandles, sessions) -> { added, removed }` where `added` is the array of *session objects* in `sessions` whose handle isn't in `existingHandles`, and `removed` is the array of handles in `existingHandles` not present in `sessions`. Order preserved from `sessions` (added) and `existingHandles` (removed).

**Acceptance (node):** new session → added; vanished session → removed; unchanged → neither; empty existing + 2 sessions → both added; empty sessions + 2 existing → both removed.

TDD → commit.

---

### Task 3: Multi-tab controller (frontend rewrite)

**Files:** Rewrite `app.js`; restructure `index.html`; extend `base.css`.

**`index.html` topology:**
```
#app
  #tabbar         — chips (one per tab) + "+" button
  #statusbar      — active tab: state dot, handle, metrics
  #panes          — one .pane per tab; only the active one is shown
  #composer       — #input
#modal-root        — agent picker overlay (empty until opened)
```

**Per-tab state (`tabs: Map<handle, Tab>`):**
`{ handle, agent, blocks:[], nodes:[], paneEl, transcriptEl, chipEl, dotEl, state:"ready", metrics:"", unseen:false }`

**Controller behavior:**
- On connect: `client.subscribeGlobal("session_list")`. The `session_list` handler runs `reconcileTabs` → `createTab` for added, `removeTab` for removed. If no active tab and tabs exist, activate the first. If the list is empty on first paint, auto-open the picker (or auto-spawn the default — keep S2's "spawn first agent" as the empty-state default).
- `createTab(handle, agent)`: build a `.pane` + `.transcript` + a chip (handle label, state dot, × close); `client.subscribe(handle)`; store. Newly created tabs start hidden.
- `activateTab(handle)`: hide all panes (`hidden` class), show this pane; mark chip active; clear `unseen` + chip marker; repaint `#statusbar` from the tab; focus `#input`.
- `removeTab(handle)`: `client.unsubscribe?`/just drop; remove DOM; delete; if it was active, activate another (or clear).
- Routing: `onEvent/onState/onInbox(frame)` → `tab = tabs.get(frame.handle)`; render into `tab.transcriptEl` (reuse S2's coalesce + markdown logic, now scoped to the tab's `blocks/nodes`). If `tab !== active`: set `tab.unseen = true`, update chip marker, pulse `document.title`. `onState` updates `tab.state` + chip dot (+ statusbar if active).
- `window_reset(frame)` → clear that tab's blocks/nodes/transcript.
- Composer: `#input` Enter → `deliver({handle: activeHandle, message})`. Esc (no modal, input blurred) → `interrupt_session(activeHandle)`. (Keep S2's early-attach + autogrow.)
- Agent picker: `openPicker()` builds an overlay in `#modal-root` listing `list_agents()`; click an agent → `spawn_session({agent_profile})`; the broadcast `session_list` creates+lets us activate the new tab; close overlay. Ctrl+N and the "+" chip open it; Esc closes it (and that Esc must not also interrupt).
- Tab close: chip × → if `tab.state === "working"`, `confirm()` first → `close_session(handle)` (broadcast removes the tab). Ctrl+W closes the active tab the same way.
- Title pulse: `document.title = (anyUnseen ? "* " : "") + "aegis"`.

**`base.css` additions:** `#tabbar` (flex row, scrollable), `.chip` (+ `.active`, `.unseen::after "*"`, `.chip .dot`, `.chip .close`), `#panes`/`.pane` (`.pane.hidden { display:none }`), `.modal-overlay` + `.agent-list`/`.agent-item`.

**Acceptance:** `node --check` clean; the S2 route tests still pass (assets served); the index references resolve. DOM behavior verified by the Task 4 smoke.

Commit.

---

### Task 4: Visual smoke (multi-tab)

**Verification gate (saidkick → real Chrome, with Alex's go-ahead).**

1. Launch `aegis web` (throwaway project) with a real agent profile.
2. Load the page → one tab auto-created. Open the picker (Ctrl+N / "+"), spawn a second agent → second chip appears, becomes active.
3. Send a message in tab 2; switch to tab 1 (chip click) → tab 1's transcript shows; tab 2 keeps its content.
4. While on tab 1, send a message to tab 2's agent isn't possible (it's not active) — instead: send on tab 2, switch to tab 1, send on tab 2 again from… simplest: trigger activity on the inactive tab (its agent finishes) → confirm the chip shows an unseen marker + the title pulses.
5. Close a tab via its × → chip disappears.
6. Screenshot each key state. Tear down.

Report screenshots. Per the standing rule this stays inline.

---

## Final verification

- [ ] `uv run pytest tests/test_web_*.py -q` green; `uv run pytest -q -m "not live"` no new regressions.
- [ ] `node tests/web/tabs.test.mjs` + `coalesce` + `markdown` exit 0.
- [ ] `node --check` clean on all JS modules.
- [ ] Multi-tab smoke screenshots show: 2 tabs, switch, unseen marker, close.

## Self-Review

**Coverage:** live session_list (T1), tab reconciliation (T2), tabbar/panes/picker/switch/close/signaling (T3), end-to-end (T4). **Deferred (documented):** the optional bell (off by default — skipped); "remote" indicator for tabs opened in another window (the tab still appears via session_list; a distinct indicator is cosmetic, deferred); external (non-web) session lifecycle reflected only on reconnect/next broadcast. **Frame-shape change** (session_list → full snapshot) is noted in Global Constraints.
