# Aegis Web Client — Design

**Status:** draft
**Date:** 2026-06-19
**Scope:** new `src/aegis/web/` package, shared theme + render refactor, TUI
migration to WS-client mode. End-state: TUI, web (desktop), and Telegram are all
thin frontends of a single `aegis serve` backend.

## Problem

Aegis today has two frontends — a full-screen Textual TUI (`aegis`) and a
Telegram bot (`aegis serve` with a `telegram:` block). The TUI owns session
state in-process; Telegram runs inside `aegis serve` and observes a separate
`SessionManager` over the same `add_event_observer` / `add_state_observer` /
`add_inbox_observer` seams.

We want a **first-class web client (desktop)** that has exact feature parity
with the TUI — same multi-tab model, same transcript fidelity, same dashboards,
same keyboard shortcuts, same themes. Telegram remains second-class. A
follow-on mobile-web variant is anticipated but out of scope here.

The web client must coexist with the TUI such that sessions started in one
are visible and continuable in the other (session sharing across frontends).
This requires the TUI to also become a WS client of `aegis serve` — the
biggest structural shift in this work and the lever that makes the cross-
frontend story tractable.

## Foundational decisions

These were settled during brainstorming and are load-bearing for the rest of
the design:

1. **Runtime: client-server from day one.** `aegis serve` is the only place
   session state lives. The web client is a separate JS/HTML app talking
   over WebSocket. Mobile-web later reuses the same protocol.
2. **Visual idiom: hybrid.** The transcript pane mirrors the TUI bit-for-bit
   (monospace, kind icons, plan glyphs, diff previews — `render_event_html`
   sibling to the existing `render_event`). The chrome (tabs, agent picker,
   queue dashboard, group dashboard, config panel, file viewer, modals)
   uses native web idioms — proportional fonts where prose fits, mouse-first
   layouts, real modals.
3. **Tech stack: HTMX for chrome, dedicated JS module for the transcript.**
   HTMX/Jinja-fragment for every request-response surface; a small vanilla-JS
   transcript module owns the live-streaming state. No SPA framework, no
   build step.
4. **TUI coexistence: TUI also becomes a WS client.** The protocol gets
   designed once and serves both frontends. Session sharing across TUI ↔ web
   ↔ Telegram works on day one of the final slice. The current in-process
   TUI keeps working behind a `--classic` flag during the migration.
5. **Slicing: vertical, foundation-first.** Ten slices (S1–S10). End of S2
   is a usable single-tab web client. End of S6 is feature parity. S9–S10
   complete the TUI refactor.

## Architecture

### Process model

**`aegis serve`** — long-lived backend. Owns `SessionManager`, the queue
substrate, the inbox router, the scheduler, the MCP plane (`AegisMCP`), and
the new HTTP/WS server. Telegram + web + (eventually) TUI are subscribers.

**`aegis web`** — convenience command. Launches a co-resident `aegis serve`
on a free localhost port if one isn't already running, opens the browser to
it. Disowns the daemon on exit; configurable to keep it running.

**`aegis`** (TUI) — through S8 continues to be a self-contained in-process
TUI exactly as today. From S9, opt-in `--remote` flag runs the TUI as a WS
client of `aegis serve` (auto-launches if missing). At S10, `--remote`
becomes the default and `--classic` retains the old path for one release.

### Three I/O surfaces on `aegis serve`

1. **HTTP** — HTMX targets. Server-rendered Jinja fragments for chrome
   (tabs, modals, status bars, dashboards). Routes like `GET /`, `POST /tabs`,
   `POST /sessions/{id}/turn`, `GET /sessions/{id}/transcript`, `GET /file`.
2. **WebSocket** — live streams + RPC. One WS per browser window/tab.
   Multiplexes per-session subscriptions and global subscriptions
   (`session_list`, `queue_digest`, `groups`). Detailed in §"WS protocol".
3. **MCP** — unchanged. Agents call back into the substrate exactly as today.

### Data flow — a typical turn

1. User types into the web input → HTMX `POST /sessions/{id}/turn` with body.
2. Server calls `manager.deliver(session_id, msg)` (same call the TUI makes).
3. `SessionManager` emits events through its existing observer channels.
4. A new `WebFrontend` (sibling to `TelegramFrontend`) listens to those
   observers, fans out to per-WS subscription registries, serializes each
   event into a `{type: "stream", kind: "event", session_id, seq, ...}`
   message and pushes it onto every interested WS.
5. Client-side JS receives the message, applies kind-coalescing
   (mirrors `aegis.render.coalesce_chunks`), and either appends a new
   rendered block to the transcript DOM or mutates an in-flight streaming
   block.

**Key invariant.** The `SessionManager` public surface does not change.
Adding the web frontend is structurally identical to how Telegram was
added — a new module consuming the same observer seams.

## WebSocket protocol

### Connection model

One WS per browser window/tab. That single connection multiplexes events
for every conversation-tab the user has open inside that window, plus global
streams. Mirrors the TUI process today: one event loop, N pane subscriptions.

### Framing

JSON messages with a `type` discriminator. Four kinds:

| Direction | Type | Purpose |
|-----------|------|---------|
| C → S | `rpc` | Request with `id`, awaits response |
| C → S | `subscribe` / `unsubscribe` | Toggle per-session or global stream membership |
| C → S | `resume` | Reconnect with per-subscription `last_seq` |
| S → C | `rpc_response` | Response by `id` |
| S → C | `stream` | Server-pushed event with `(session_id, seq)` or global tag |

### RPC surface (v1, evolves per slice)

```
spawn_session(agent_profile, cwd=None)        → {session_id, handle}
close_session(session_id)                      → ok
interrupt_session(session_id)                  → ok
deliver(session_id, message)                   → ok
list_sessions()                                → [{session_id, handle, agent, state, started_at, last_event_at}, ...]
list_agents()                                  → [{name, profile}, ...]
```

Grows in later slices: `handoff`, `enqueue`, `pick_file`, `get_file_content`,
queue/group/schedule introspection. Each slice adds only the calls it needs;
no speculative surface.

### Stream messages (server → client)

Discriminated by `kind`:

```
{type: "stream", kind: "event",        session_id, seq, event_type, html, payload_text, raw}
{type: "stream", kind: "state",        session_id, seq, state, metrics}
{type: "stream", kind: "inbox",        session_id, seq, msg}
{type: "stream", kind: "session_list", added: [...], removed: [...], updated: [...]}
{type: "stream", kind: "queue_digest", queues: [...]}
{type: "stream", kind: "group_state",  group_name, members, current_broadcast, recent}
{type: "stream", kind: "window_anchor",  session_id, oldest_seq, current_seq}
{type: "stream", kind: "history_complete", session_id, current_seq}
{type: "stream", kind: "window_reset", session_id, dropped_through_seq}
```

**`event_type`** is one of the canonical types from the existing event
codec (`AssistantText`, `AssistantThinking`, `ToolUse`, `ToolResult`,
`AgentPlan`, `Result`, `ContextUpdate`, `SystemInit`, etc.). The codec
already does legacy-record decode, which is now load-bearing for client/
server version skew.

**`html`** is the server-rendered HTML fragment produced by the new
`render_event_html` (see §"Shared rendering pipeline"). Present for
events that mount as complete units (tool results, plan blocks, etc.).
**`payload_text`** is the same plaintext payload `_history` stores in the
TUI (for copy-to-clipboard). **`raw`** is the typed event for events that
require client-side aggregation (streaming text/thinking chunks); in those
cases `html` may be absent and the client constructs the rendered block from
`raw`.

### Subscriptions

```
{type: "subscribe", target: {kind: "session", session_id: "..."}}
{type: "subscribe", target: {kind: "global",  stream: "session_list"}}
{type: "unsubscribe", target: ...}
```

On `subscribe(kind: "session", session_id)`, the server reads the full JSONL
for that session and streams every event with its `seq`, then sends
`{type: "stream", kind: "history_complete", session_id, current_seq}`.
Live events from `current_seq + 1` onward stream normally.

For huge sessions on slow remote links, a future `max_history_events: N`
param caps the initial send. Deferred from v1.

### Reconnection

- Every persisted event carries a monotonically increasing `seq` per
  `session_id`. The substrate already persists each session's event stream
  as JSONL under `.aegis/state/sessions/<session_id>/events.jsonl`. `seq` is
  the line number plus a small in-memory counter for events emitted after
  the last flush.
- Client tracks the highest `seq` it has received per subscribed session.
- On WS reconnect, after auth, client sends the first frame as
  `{type: "resume", subscriptions: [{session_id, last_seq}, ...], globals: [...]}`.
- Server behavior per subscription:
  - **Small gap** (`current_seq - last_seq ≤ RESUME_GAP_CAP`, default 1000):
    server streams just `last_seq + 1 .. current_seq`, then live events
    resume.
  - **Large gap or first-ever**: server treats it as a fresh subscribe.
    Sends `{type: "stream", kind: "window_reset", session_id, dropped_through_seq}`
    so the client knows to discard its stale tail, then streams full history
    just like a fresh `subscribe`.
- During the disconnect, the session itself never pauses — agents keep
  running, events keep flushing to JSONL. The user sees a "reconnecting…"
  indicator in the status bar.
- Server sends ping frames every 30s; client treats ≥60s absence as
  disconnect and starts reconnect-with-backoff.

**No in-memory ring buffer in v1.** Reads come from JSONL on subscribe +
resume; live events stream from in-memory observers. If disk pressure
becomes a problem, a ring cache slots in transparently as a layer in front
of the same resume path. Worth a quick audit of JSONL fsync semantics
during S1 before S2 commits.

### Persistence reality check (S1 audit, 2026-06-30)

Grounding the resume protocol against `src/aegis/state/session_log.py` as it
actually exists today surfaced four deltas S2 must design around:

1. **Path is handle-named, not session-id-keyed.** Events persist at
   `<state_dir>/sessions/<handle>.jsonl` (flat, one file per tab), via
   `session_log_path(state_dir, handle)`. Update the protocol's
   "JSONL under `.aegis/state/sessions/<session_id>/events.jsonl`" wording —
   the resume reader keys off the tab handle.
2. **No per-line `seq` on disk.** Each line is
   `{"v": 1, "aegis_ts": <iso>, "event": <encoded>}`. `seq` must be
   *synthesized* on read as the 1-based line index. The in-memory counter
   for post-flush live events (S2's `current_seq`) starts from that line
   count. There is no stored monotonic id to rely on.
3. **No `fsync`.** `append_event` flushes on context-manager close but does
   not `os.fsync`. Acceptable for v1 (single-user, append-only), but the
   resume path must tolerate a partially-written trailing line after a crash.
4. **`replay_events` is not torn-line tolerant.** It calls `json.loads`
   per line with no guard; a torn final line raises. S2's history reader
   must wrap the final-line decode in a try/except and drop an unparseable
   trailing line — mirror the tolerant replay in `groups/persistence.py`.

None of these block S1; they retarget S2's "JSONL history reader + resume"
work at the real format.

### Auth

First WS frame after upgrade: `{type: "auth", token: "..."}`. Server
validates against the configured token. Valid →
`{type: "hello", server_version, protocol_version, constants: {N_MAX, EVICT_BATCH, LOAD_BATCH, STICKY_EPS, LOAD_MORE_EPS, DEBOUNCE_S, RESUME_GAP_CAP}, supported_kinds: [...]}`.
Invalid or 5s timeout → server closes WS with code 4401.

### Backpressure

Per-WS send queue capped (default 10k messages). If client falls behind,
server closes the WS with reason `backpressure`; client reconnects via
`resume`, which catches it up cleanly. Durable events are never dropped
silently — they're persisted to JSONL regardless. The WS is just a delivery
channel; persistence is the source of truth.

### Protocol versioning

`protocol_version` in `hello`. Client refuses to operate if server
major-version is higher than what the client was built for; banner asks for
refresh. Minor-version skew allowed (forward-compat). Event-shape evolution
relies on the codec's legacy-record decode.

## Auth & binding

### Single token

On first invocation of `aegis web` (or `aegis serve --web`), if no token is
configured, generate a 32-byte random token, write it to `.aegis.yaml` under
`web.token` via the comment-preserving `aegis.config.edit` helpers, print the
bookmarkable URL to stdout. Subsequent invocations re-use it.

### Binding defaults

- `aegis web` → binds `127.0.0.1` only. Token still required (defense in
  depth). Surface is purely local.
- `aegis serve --web` → reads from `web.bind` in `.aegis.yaml`. Default
  `127.0.0.1`. Common override: bind to the Tailscale interface for
  cross-device access. `0.0.0.0` allowed but logs a warning at boot.
- Port from `web.port` or auto-picked free port. Auto-picked port persists
  to `.aegis/state/web.port` so a stable bookmark URL works across restarts.

### Token flow

1. Bookmark URL: `http://<host>:<port>/?t=<token>`.
2. On first page load, JS reads `?t=` from URL, stores in `localStorage`,
   then `history.replaceState` strips it from the address bar.
3. All HTMX requests inject `Authorization: Bearer <token>` via
   `htmx:configRequest`.
4. WS upgrade carries `?t=<token>` as a query param (browsers can't set
   headers on WS open).
5. First WS frame after upgrade is the explicit `{type: "auth", token}`
   check.

### HTTP auth flow

Every request must carry `Authorization: Bearer <token>` OR `?t=<token>`
query string OR `aegis_token` cookie. Missing/invalid → 401. CSRF surface is
zero by default — HTMX uses the explicit `Authorization` header, no ambient
cookie auth.

### Lost token / rotation

- `aegis web --show-url` reprints the bookmarkable URL.
- `aegis config web rotate-token` regenerates the token and hot-reloads
  via the existing scheduler-style reload path.

### Multi-user / multi-device

Out of scope for v1. Single shared secret. If later wanted, the storage
layer is already a list — additive change.

### Logging

The token in the URL gets logged in access logs. Acceptable for a single-
user system over localhost/Tailscale; documented. Anyone with access to
`.aegis/logs/` already has access to `.aegis.yaml`.

## Backend integration

### Package layout

```
src/aegis/web/
  __init__.py
  frontend.py       # WebFrontend — sibling to TelegramFrontend, lifecycle owner
  server.py         # FastAPI app: HTTP routes + WS endpoint
  ws.py             # WSSession — one per connected browser window, multiplexes
  subscriptions.py  # SubscriptionRegistry — per-session observer fan-out;
                    # one observer per session shared across all subscribed WSs
  history.py        # JSONL tail reader for subscribe + resume
  routes/
    chrome.py       # GET /, /tabs, /sessions/{id}/transcript, ...
    sessions.py     # POST /sessions, POST /sessions/{id}/turn, ...
    files.py        # GET /file, GET /files/picker, ... (S6)
    config.py       # GET /config, POST /config/agent, ... (S6)
  templates/
    base.html
    tab.html
    transcript_block.html
    agent_picker.html
    ...
  static/
    css/
      base.css
      transcript.css
      themes/<theme>.css   # generated from theme YAML at boot
    js/
      transcript.js        # _history, _window_start, eviction, scroll-up,
                           # streaming aggregation. Talks to ws.js.
      ws.js                # WS client: auth, RPC, subscribe, resume,
                           # reconnect-with-backoff, ping/pong, dispatch
      htmx-config.js       # Authorization header injection + helpers
      app.js               # Boot: read ?t=, init ws.js, wire HTMX
```

### Mounting into `aegis serve`

Mirrors how Telegram is wired today:

```python
if config.web:
    web_frontend = WebFrontend(manager, config.web)
    await web_frontend.start()  # uvicorn on configured bind/port
    register_shutdown(web_frontend.close)
```

`WebFrontend.start()` wires the frontend as an observer on `SessionManager`
(for cross-cut streams) and starts uvicorn inside the existing asyncio loop.

### Shared rendering pipeline

The JS transcript module renders the same event types the TUI renders, but
the per-kind formatter logic (kind icon, path-tail, diff structure, plan
glyphs) lives in Python. Two strategies coexist:

1. **Server-rendered HTML for unit blocks.** Tool results, plan blocks,
   diffs, terminator lines — anything that mounts as a complete unit.
   `aegis.render.render_event_html(event, palette) -> str` is added as a
   sibling to the existing `render_event`. Both share a per-kind formatter
   registry — restructure `render.py` into a thin dispatcher + per-kind
   formatters that emit either Rich renderables or HTML.
2. **Client-side aggregation for streaming.** `AssistantText` /
   `AssistantThinking` chunks. Server pushes raw chunks tagged with
   `(type, message_id)`; client coalesces and mutates the in-flight block's
   text content. Same logic as `aegis.render.coalesce_chunks`.

### `AppBridge` additions

Likely one new method: `bulk_session_metadata(session_ids: list[str]) -> list[dict]`
for the initial session-list paint on page load. Additive; doesn't break
existing implementers.

### Plugin substrate compatibility

The web frontend is just another consumer of the same observer streams
plugins extend via `@hook`. Plugins that mutate events via `pre_turn` are
seen by web identically to TUI. Plugins that add `@tool`s show up in the
agent's tool calls and render via the same event types. No special
plugin-vs-web seam.

## Themes — shared YAML

### Layout

```
src/aegis/data/themes/      # bundled, ships with the package
  aegis-ink.yaml            # current default
  <future themes>.yaml
.aegis/themes/<name>.yaml   # user overlays — same drop-in convention
                            # as plugins/schedules/groups
```

Matches the existing `src/aegis/data/models.yaml` precedent.

### Schema

```yaml
name: aegis-ink
description: Calm near-black with amber accent.

palette:                       # shared core — both renderers read this
  bg:       "#0a0a0a"
  fg:       "#e8e8e8"
  accent:   "#e6a155"
  muted:    "#666666"
  border:   "#1f1f1f"
  state:
    idle:        "#888888"
    running:     "#e6a155"
    interrupted: "#cccc66"
    done:        "#9ade9d"
    error:       "#d97a7a"
  events:
    read:    { icon: "📖", color: "#7fc6e8" }
    write:   { icon: "✏️", color: "#e6a155" }
    bash:    { icon: "⌬",  color: "#d99ad9" }
    grep:    { icon: "🔎", color: "#7fc6e8" }
    edit:    { icon: "✻",  color: "#e6a155" }
    web:     { icon: "🌐", color: "#7fc6e8" }
    handoff: { icon: "➡️", color: "#e6a155" }
    delete:  { icon: "🗑",  color: "#d97a7a" }
    retry:   { icon: "🔄", color: "#cccc66" }
    other:   { icon: "⏺",  color: "#888888" }
  plan:
    completed:   "#9ade9d"     # ● glyph
    in_progress: "#e6a155"     # ◐ glyph
    pending:     "#888888"     # ○ glyph
  diff:
    added:   "#9ade9d"
    removed: "#d97a7a"
  cost:
    low:  "#888888"
    mid:  "#e6a155"
    high: "#d97a7a"

layout:                         # shared layout knobs
  blank_rows_between_turns: 1

tui:                            # TUI-only refinements
  border_style: "round"
  scroll_bar_color: "#1f1f1f"

web:                            # web-only refinements
  font_family_mono:  "'Berkeley Mono', 'JetBrains Mono', monospace"
  font_family_prose: "'Inter', system-ui, sans-serif"
  font_size_base:    "14px"
  line_height:       "1.5"
```

### Loader

New module `src/aegis/themes/__init__.py` (extracted from `tui/themes.py`):

- `load_theme(name) -> Theme` reads `src/aegis/data/themes/<name>.yaml`,
  merges `.aegis/themes/<name>.yaml` if present. Fail-loud on missing
  required fields.
- `Theme` dataclass carries parsed sections.
- `Theme.to_aegis_colors() -> AegisColors` — what the TUI consumes today.
  `tui/themes.py` becomes a thin shim.
- `Theme.to_css_variables() -> str` — emits `:root { --aegis-bg: ...; ... }`
  for the web.

### Web pipeline

On `WebFrontend.start()`, load the configured theme. Generate CSS variables
once. Serve as `/static/css/themes/<name>.css`. Base `transcript.css` and
`chrome.css` reference variables: `color: var(--aegis-fg)`,
`background: var(--aegis-event-read-color)`, etc.

Server-rendered Jinja for event blocks uses event kind to pick a CSS class
whose color comes from a theme variable. Theme switching at runtime (S8)
swaps the `<link rel="stylesheet">` href; variables flip live without reload.

### Shared transcript constants

Move `N_MAX`, `EVICT_BATCH`, `LOAD_BATCH`, `STICKY_EPS`, `LOAD_MORE_EPS`,
`DEBOUNCE_S` from `tui/pane.py` to a single source of truth
(`src/aegis/render/transcript_constants.py`). TUI imports them; the web
server reads them and includes them in the `hello` message; JS client uses
the server-supplied values. Single tuning knob.

## Client-side composition

### Window topology

```
┌──────────────────────────────────────────────────┐
│ TabBar — one chip per conversation tab + "+"     │  HTMX
├──────────────────────────────────────────────────┤
│ QueueStrip — always-on queue digest              │  HTMX (S4)
├──────────────────────────────────────────────────┤
│                                                   │
│ ConversationPane (active tab)                    │  JS transcript module
│   [transcript scroll area — JS-owned]            │
│                                                   │
├──────────────────────────────────────────────────┤
│ Input — growing textarea                          │  HTMX + tiny JS
├──────────────────────────────────────────────────┤
│ StatusBar — handle, agent, state dot, metrics    │  HTMX + WS state push
└──────────────────────────────────────────────────┘

Modals (overlay): AgentPicker, QueueDashboard, GroupDashboard,
ConfigPanel, FilePicker, FileViewer, SessionHistory.
```

### `ws.js`

Owns the single WebSocket. Exposes:

- `ws.rpc(method, params) -> Promise<result>` (id-tagged request/response).
- `ws.subscribe(session_id) -> handle`.
- `ws.subscribe_global(kind) -> handle`.
- `ws.on(kind, fn)`; per-handle dispatchers.
- Reconnect-with-backoff loop. Tracks per-session `last_seq`; on reconnect,
  issues `resume` with the gathered last-seqs.

### `transcript.js` — TUI parity, line for line

One instance per mounted ConversationPane. Mirrors `pane.py`:

```js
_history:                BlockRecord[]    // full session events in RAM
_window_start:           number            // first mounted DOM block index
_stick_to_bottom:        boolean
_loading_older:          boolean           // guards re-entry on scroll-up
_streaming_history_idx:  number | null    // currently-streaming block index
```

- `_on_event(ev)` dispatches by `ev.kind` like `pane._on_core_event`.
  Streaming chunks call `_stream_append`; complete blocks call `_mount_block`.
- Scroll-up debounced via `setTimeout(DEBOUNCE_S * 1000, _load_older)`.
  Local DOM operation: capture first mounted block's `getBoundingClientRect().y`,
  mount older `BlockRecord`s above it, restore scroll so that block stays at
  the same Y. Identical algorithm to `tui/pane.py:_load_older`.
- Eviction gated on `_stick_to_bottom`. Same `N_MAX` threshold, same
  `EVICT_BATCH` count.
- Streaming aggregation: first chunk creates `BlockRecord` + DOM node, stores
  `_streaming_history_idx`. Subsequent chunks mutate the record's
  `payload`/innerHTML and the DOM node in place. `_flush_streaming` on turn
  end clears the index.
- Sticky-bottom tracked via `IntersectionObserver` on a sentinel at the
  scroll bottom — no scroll-event spam, behavior identical to the TUI's
  `STICKY_EPS`.
- Theme colors read via `getComputedStyle(document.documentElement)
  .getPropertyValue('--aegis-event-read-color')` — no theme state in JS.

### HTMX chrome patterns

- **TabBar.** `POST /tabs` opens a new tab (server creates session, returns
  the new tab's HTML and the active-tab HTML). Click a tab →
  `GET /tabs/{id}/activate` swaps the active pane via `hx-target`. The
  transcript module is destroyed/reinitialized as panes switch.
- **AgentPicker.** Ctrl+N → `GET /modals/agent-picker`. Picker is a list of
  agents from `.aegis.yaml`. Click → `POST /tabs?agent=<name>`.
- **StatusBar.** Per-tab fragment served by `GET /tabs/{id}/statusbar`.
  Patched in by a small client-side update when `stream/state` messages
  arrive — server pre-renders the new HTML and pushes it; client swaps
  `innerHTML` of `#statusbar-<tab-id>`.
- **Modals.** Generic modal stack. Each modal is `GET /modals/<name>`
  returning HTML; overlay mount; Esc closes top of stack.
- **Dashboards.** Queue (Ctrl+D), Group (Ctrl+G). Server-rendered shell +
  subscription to relevant global stream; rows update via innerHTML patch.

### Keyboard shortcuts

Document-level handler in `app.js`. Same chords as TUI:

| Chord | Action |
|-------|--------|
| Ctrl+N | Agent picker (new tab) |
| Ctrl+W | Close current tab (confirmation if running) |
| Ctrl+Tab / Ctrl+Shift+Tab | Cycle tabs |
| Ctrl+D | Queue dashboard |
| Ctrl+G | Group dashboard |
| Ctrl+H | Session history (S7) |
| Ctrl+P | File picker (S6) |
| Ctrl+T | Theme picker (S8) |
| F2 | Config panel (S6) |
| Esc | Interrupt current session (when no modal open + input unfocused) |
| Ctrl+L | Clear current transcript view (DOM only, `_history` retained) |
| / | Focus input |

Browser collisions exist (Ctrl+W = close window, Ctrl+N = new window).
`preventDefault` is attempted and documented. An Electron wrapper would
resolve them; out of scope for v1.

### Cross-tab signaling

When an event arrives for a non-active tab:

- TabBar chip shows a state dot (color from `palette.state.*`).
- Sticky `*` next to the tab title until the user focuses that tab.
- Document title pulses: `"* aegis — <active-tab-handle>"`.
- Optional bell on terminal events for a background tab (`Result`-with-
  stop_reason). Web Audio API synth — no audio file. Configurable per
  `web.bell_enabled`, default off.

### Lazy session start

Mirrors TUI v0.10+ polish. `POST /tabs/draft` returns a tab with no session
attached. Harness subprocess spawns only when the first message is sent.

### Cross-window coherence

Two browser windows pointing at the same `aegis serve` see the same sessions
(both subscribe to `session_list`). A tab opened in window A appears in
window B's TabBar with a "remote" indicator. Switching to it in window B
subscribes B's WS to the session — both windows watch the same transcript
concurrently.

## Slice plan

| # | Slice | Acceptance | ~Effort |
|---|-------|-----------|---------|
| **S1** | **Refactor: theme YAML + shared render layer** | (a) `src/aegis/data/themes/aegis-ink.yaml` exists with full schema; loader in `src/aegis/themes/__init__.py` builds `Theme` from YAML, exposes `to_aegis_colors()` + `to_css_variables()`. (b) `tui/themes.py` is a thin shim; existing TUI snapshot tests pass unchanged. (c) `aegis.render.render_event_html(event, palette) -> str` sibling to `render_event`; both share a per-kind formatter registry. (d) HTML-renderer tests cover every event kind via golden HTML files. (e) Transcript constants moved to single source of truth. (f) JSONL fsync semantics audited. | ~0.5d |
| **S2** | **Web foundation — single-tab end-to-end** | `aegis web` launches `aegis serve` if missing + opens browser. Single conversation: spawn from agent picker, see events stream with full transcript fidelity (kind icons, plan blocks, diffs, coalescing, cost/usage on terminator), type message, interrupt with Esc, close. WS protocol working: auth, hello (with constants), subscribe (full history), live event/state/inbox streams, resume. Transcript windowing parity: `N_MAX` eviction when sticky-bottom, debounced scroll-up local re-mount, anchor preservation, streaming aggregation. `aegis-ink` theme renders. Tests: integration test driving WS protocol end-to-end against a fake `SessionManager`; JS transcript module tested in Playwright (or jsdom). | ~1.5d |
| **S3** | **Multi-tab + agent picker + cross-tab signaling** | TabBar with N tabs, Ctrl+N opens AgentPicker modal, click an agent spawns a tab. State dot on chip, sticky `*`, document title pulse, optional bell. Tab close with confirmation if running. Two browser windows on same `aegis serve` see the same tab list. | ~0.5d |
| **S4** | **Queue dashboard + status line** | StatusBar (handle, agent, state, metrics) per tab, driven by `stream/state` messages. Always-on QueueStrip above tab (single-row adaptive format). Ctrl+D opens QueueDashboard modal: `QUEUES / IN-FLIGHT / QUEUED / RECENT` bands, DetailPanel with payload/lifecycle/assistant-text tail. ↑↓ navigation, `>` jumps to worker's tab, Esc closes. Server side reuses the existing `QueueDigest` aggregator. | ~0.5d |
| **S5** | **Group dashboard** | Ctrl+G opens GroupDashboard modal: Members / Current broadcast / Recent broadcasts panels. Reuses the existing `render_dashboard` pure function — port the data shape to HTML. Live updates via `group_state` global stream. | ~0.5d |
| **S6** | **Config panel + file viewer + file picker** | F2 opens ConfigPanel modal: list agents, queues, schedules; add/edit/remove rows via `aegis.config.edit` helpers. AddAgentModal port. Ctrl+P opens FilePicker. FileTab opens viewer with Pygments server-side syntax highlighting. Ctrl+X → `xdg-open` (only when bound to localhost). | ~1d |
| **S7** | **Session history (Ctrl+H)** | Depends on TUI session-history shipping first (currently designed, not implemented per TASKS.md). Modal lists every user-initiated session (open/closed, current process or previous). Reopens via jump-to-tab, `drv.resume()`, or fresh spawn with recorded profile+cwd. Reuses the backend reads that TUI's slice 1 will introduce. | ~0.5–1d |
| **S8** | **Theme switcher + additional themes** | Theme picker modal (Ctrl+T). Runtime swap via `<link rel="stylesheet">` href change — no reload. Two or three additional bundled themes (light, high-contrast). User-overlay themes from `.aegis/themes/` listed alongside bundled. | ~0.5d |
| **S9** | **TUI as WS client (behind `--remote` flag)** | New module `src/aegis/tui/ws_client.py` — Python WS client mirroring the JS one. `aegis --remote` runs the TUI with `manager` swapped for `RemoteSessionManager` (implements the same `AppBridge` Protocol; methods are RPC calls; observer wiring becomes WS subscriptions). Auto-launches co-resident `aegis serve` on localhost if not already running. Classic in-process TUI remains the default; `--remote` is opt-in for testing. | ~1.5d |
| **S10** | **TUI WS becomes default, classic moved to `--classic`** | Flip default. `--classic` retained for one release. Cleanup pass on dual code paths in TUI bootstrap. CHANGELOG entry calling out the architectural shift. After one release, `--classic` removed and the in-process path deleted. | ~0.5d |

### Stop points

Every slice is an honest stop point. The most likely "ship and pause" points:

- **After S2**: working single-tab web client. Use it; gather UX intuition.
- **After S6**: web client at full TUI feature parity for daily use. Sessions
  not shared between TUI and web yet.
- **After S10**: full architectural unification. Single backend, three
  frontends. Session sharing across all three.

### Sequencing constraints

- S1 strictly first (shared infra).
- S2 strictly after S1.
- S3–S6 can be reordered or partially parallelized.
- S7 has the TUI dependency; if TUI session-history hasn't shipped, S7 either
  waits or co-ships.
- S8 can land anywhere after S2.
- S9 needs the WS protocol feature-complete (post-S6).
- S10 strictly after S9 has been daily-driven for at least a week.

## TUI migration

The biggest structural shift in this work and the lever that makes session
sharing across frontends tractable. Detailed treatment because S9 is the
second-largest novelty after S2.

### What changes inside the TUI

Most of the TUI is already client-shaped — the renderer, theme engine,
transcript windowing, modals, input, panes all consume typed events that
come from somewhere. Whether "somewhere" is `session.add_event_observer(...)`
in-process or `ws.on("event", ...)` over the wire doesn't change the
rendering, the windowing, or the chrome.

Concrete changes:

1. **`pane.handle_user_input`** currently calls `session.deliver(...)`.
   Becomes `await self.client.deliver(session_id, ...)`.
2. **`app.spawn_session()`** currently calls `manager.spawn(agent)`. Becomes
   `await self.client.spawn(agent)`.
3. **`pane.on_event`** already takes typed events. If the WS client
   deserializes into the same typed events, `pane.on_event` doesn't change.
4. **Startup session-list sync** — new. Today's TUI starts with no sessions.
   The remote TUI subscribes to `session_list` on connect; if `aegis serve`
   already has sessions running, they appear immediately as tabs.
5. **Reconnection logic** — new. Same protocol as the web client; same
   `last_seq`/`resume` flow.
6. **MCP plane** moves out of the TUI process entirely. `AegisMCP` lives in
   `aegis serve`, binds to `SessionManager` directly. The TUI no longer
   co-hosts an MCP server — a structural cleanup.

### What stays the same

- Theme engine, `AegisColors`, `app.palette` threading.
- All Textual widgets (`ConversationPane`, `TabBar`, modals).
- Transcript windowing (`_history`, `_window_start`, sticky-bottom, eviction,
  scroll-up restoration, streaming aggregation).
- Render dispatch (`render_event` and the shared per-kind formatter registry).
- Keyboard shortcuts.
- File indexer + picker UX, file viewer.
- Config panel.
- Plugin substrate — plugins extend agent behavior, not the TUI.

### Zero-config local UX preservation

A guardrail. Today, typing `aegis` and getting a TUI requires zero setup.
The refactored TUI must preserve that.

- `aegis --remote` (and `aegis` after S10) auto-launches a co-resident
  `aegis serve` on `localhost:<port>` if one isn't already running.
- For localhost binds, no token is required for the auto-launched daemon
  (the WS still uses the auth handshake, but the token is auto-generated
  and embedded in the local config). The auto-token never leaves the local
  machine.
- Daemon either disowns on TUI exit or stays running as a configured
  service. `aegis.serve.lifecycle: tui_owned | persistent` config knob.

If "you have to remember to start `aegis serve` first" ever becomes the
daily UX, that's a regression.

### Migration safety

- S9 ships `--remote` as opt-in. Classic in-process TUI is default and
  unchanged. Daily driver continues to work; you choose when to test the new
  path.
- S10 flips the default after at least a week of you running on `--remote`
  daily. `--classic` retained for one release as a safety valve.
- One release later, `--classic` removed and in-process code deleted.

## Testing strategy

### Unit / integration test layers

- **Theme loader.** `tests/test_themes.py` — load every bundled theme; assert
  `to_aegis_colors()` matches a golden `AegisColors` for `aegis-ink`; assert
  `to_css_variables()` matches a golden CSS for `aegis-ink`. Overlay-merge
  scenarios.
- **Shared render.** `tests/test_render_html.py` — for every event kind,
  golden HTML file. `tests/test_render_rich.py` exists today; both share the
  per-kind formatter registry.
- **JSONL history reader.** `tests/test_history_reader.py` — happy path,
  truncated tail, fsync mid-stream, very long sessions.
- **WS protocol.** `tests/test_web_protocol.py` — drive a real
  `WebFrontend` against a fake `SessionManager` over an in-memory WS;
  exercise auth, subscribe, resume (small gap, large gap, first-ever),
  backpressure, ping/pong, version skew rejection.
- **HTTP routes.** `tests/test_web_routes.py` — Starlette TestClient for
  every chrome route.
- **JS transcript module.** `tests/web/test_transcript.spec.ts` — Playwright
  or jsdom. Mount the module, feed event sequences, assert DOM state +
  `_history` + `_window_start` + sticky-bottom behavior. Mirrors
  `tests/test_pane_windowing.py` for the TUI.
- **End-to-end.** `tests/test_web_e2e.py` (marked `live`) — Playwright drives
  a real browser against `aegis serve --web`; tests cover spawn → event
  stream → interrupt → close → reopen for resume.

### Performance regression checks

The TUI's transcript-windowing spec mentions snapshot tests in
`tests/test_pane_windowing.py`. Equivalent for the web client:
`tests/test_web_windowing.py` asserts on `_history.length`,
`_window_start`, mounted DOM count, after a scripted event sequence.

### Cross-frontend protocol parity

After S9 lands, add `tests/test_remote_tui_parity.py` — drives a remote
TUI through the same WS protocol used by `tests/test_web_protocol.py`;
ensures both clients can drive the same operations to the same outcomes.

### Live live-CLI tests

The repo already has a `live` marker for tests that need real `claude`/
`opencode`/`gemini`. The web client adds:

- `tests/test_web_live.py` (marked `live`) — `aegis serve --web` + a real
  `claude` subprocess + a Playwright browser; smoke a full conversation.

## Out of scope (deferred)

- Mobile-web variant. Same protocol, different layout — separate spec when
  the time comes.
- Multi-user / per-device tokens. Single shared secret in v1.
- Electron wrapper (resolves browser keyboard collisions). Separate spec
  when the time comes.
- In-memory ring buffer for short disconnects. JSONL reads are fine for v1;
  cache layer is additive.
- Per-conversation theme overrides. Single global theme in v1.
- Voice/audio I/O via web. The Telegram frontend has it open as a separate
  bucket; web doesn't follow it.
- Substrate-level `notify()` integration with web. Telegram-bucket-E concern.

## Risks

1. **S2 is the dragon.** First convergence of every new piece — protocol,
   JSONL reader, FastAPI integration, transcript JS, event rendering, theme
   rendering, auth, reconnection. Sizing at 1.5d assumes this design holds
   up. Mitigation: write the WS protocol as its own spec inside
   `docs/superpowers/specs/` *before* writing S2's plan, so the contract is
   explicit and reviewable.
2. **S9 is the second-biggest novelty.** First touch of working TUI.
   Mitigation: opt-in `--remote` with `--classic` always available; ≥1 week
   of daily-driving on `--remote` before S10 flips defaults.
3. **Browser keyboard collisions.** Ctrl+W (close window) is the irritant.
   Worth a low-priority follow-up to ship an Electron wrapper if friction
   warrants — out of scope for v1.
4. **JSONL persistence stability.** The protocol's resume flow assumes the
   existing session JSONL files are append-only, fsynced cleanly, and have
   stable event shapes. S1 audits this.
5. **Two-process debugging.** "Is it the client, the server, or the wire" is
   now a question. Logging discipline matters. The existing JSONL substrate
   helps but doesn't eliminate the friction.

## Files touched (summary)

- New: `src/aegis/web/` package (frontend, server, ws, subscriptions,
  history, routes, templates, static).
- New: `src/aegis/themes/__init__.py` (theme loader, `Theme` dataclass).
- New: `src/aegis/data/themes/aegis-ink.yaml` (extracted from `tui/themes.py`).
- New: `src/aegis/render/transcript_constants.py` (single source of truth
  for `N_MAX` etc.).
- New: `src/aegis/render/render_html.py` (or extension of existing
  `render.py` with HTML emitter).
- New: `src/aegis/tui/ws_client.py` (S9 — Python WS client mirroring JS).
- Modified: `src/aegis/tui/themes.py` (becomes thin shim over the new loader).
- Modified: `src/aegis/tui/pane.py` (import constants from new location).
- Modified: `src/aegis/render.py` (factor per-kind formatter registry).
- Modified: `src/aegis/serve.py` (mount `WebFrontend` when configured).
- Modified: `src/aegis/config/__init__.py` (parse `web:` block from `.aegis.yaml`).
- Modified: `src/aegis/cli.py` (new `aegis web` command).
- Modified: `src/aegis/tui/app.py` (S9 — accept `RemoteSessionManager`).
- New tests as listed in §"Testing strategy".

## References

- TUI transcript windowing spec: `docs/superpowers/specs/2026-06-02-aegis-tui-transcript-windowing-design.md`
- Telegram frontend (reference for the second-frontend pattern):
  `src/aegis/telegram/`
- Vision doc: `vault/Atlas/Architecture/2026-05-17-aegis-vision.md`
- Harness roadmap: `vault/Atlas/Architecture/2026-05-25-aegis-harness-roadmap.md`
- Plugin substrate (compatibility surface):
  `docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`
