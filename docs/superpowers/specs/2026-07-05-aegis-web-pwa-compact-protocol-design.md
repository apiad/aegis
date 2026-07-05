# Aegis Web PWA + Compact Protocol — Design

**Status:** draft
**Date:** 2026-07-05
**Scope:** evolve the existing web client (`src/aegis/web/`, shipped S1–S8)
into a co-equal, first-class UI alongside the TUI — installable as a PWA,
mobile-first, and optimized for flaky/low-bandwidth connections via a
compact-by-default WebSocket protocol. Deprecate and remove the aegis Telegram
chat frontend as the final slice. Parent effort:
`2026-06-19-aegis-web-client-design.md`; wire contract this supersedes/extends:
`2026-06-30-aegis-web-ws-protocol-design.md`.

## Vision

Two co-equal, fully-fledged UIs over one `aegis serve` backend:

- **TUI** (`aegis`) — local development.
- **Web / PWA** (`aegis web`) — remote development first, local also; the
  mobile-first way to reach aegis running on a VPS over a flaky mobile uplink.

The current design (`2026-06-19`) positions the TUI as default, the web client
as "first-class **desktop**", mobile as out of scope, and Telegram as a
retained second-class frontend. This design inverts that: web becomes co-equal
and mobile-first, and Telegram is removed — the PWA is its replacement for
remote mobile control of aegis.

Two constraints shape everything below:

1. **Once loaded, the app talks to the backend as little as possible.** The
   wire carries responses and compact summaries by default; full detail is
   fetched on demand, one block at a time.
2. **The app must survive a flaky connection.** Installed PWA shell loads
   offline; the last transcript stays readable offline; live actions degrade to
   a visible "reconnecting…" state and catch up on resume.

## What already exists (do not rebuild)

`src/aegis/web/` is a working WS frontend of `aegis serve` (S1–S8, on `main`,
browser-verified):

- `server.py` — Starlette app (`build_web_app`): `/`, `/healthz`,
  `/theme.css`, `/ws`, `/static`.
- `wssession.py` — `WSSession`: auth → `hello` → RPC / subscribe / resume /
  live stream, bounded send queue + backpressure close.
- `subscriptions.py` — `SubscriptionRegistry` + `event_frame(handle, seq, ev)`:
  per-handle observer fan-out; session-list, queue-digest, group-status, file,
  config surfaces.
- `history.py` — `read_history(state_dir, handle)`: torn-trailing-line-tolerant
  JSONL reader, synthesizes `seq` as the 1-based line index.
- `frontend.py` — `WebFrontend`: uvicorn lifecycle owner (sibling to
  `TelegramFrontend`), token in `?t=`.
- `static/` — `index.html`, `css/base.css`, `js/{ws,app,coalesce,markdown,tabs,queues}.js`.
  No build step; vanilla JS.

Multi-tab, agent picker, cross-window session sharing, queue/group dashboards,
config panel (read + edit), file picker/viewer, theme switcher are all present.
This design changes the **wire diet**, adds **client-side rendering**, a **PWA
shell**, a **mobile layout**, a **positioning flip**, and **removes Telegram** —
it does not rebuild the working surfaces.

## Grounding (real symbols this design binds to)

- `src/aegis/web/subscriptions.py::event_frame` — today emits
  `{type, kind:"event", handle, seq, event_type, event: encode_event(ev),
  html: render_event_html(ev)}`. W1 changes this function.
- `src/aegis/state/event_codec.py::encode_event` / `decode_event` — the codec
  shape compaction operates on (field-level truncation keeps the shape valid
  for `decode_event`).
- `src/aegis/render_html.py::render_event_html` — server-side renderer; its
  per-kind logic is the reference W2 mirrors client-side. Kept for the TUI's
  own use, no longer sent over the web wire.
- `src/aegis/web/wssession.py::WSSession._call` — RPC dispatch; W1 adds
  `get_event`. `WSSession._hello` — advertises `protocol_version` +
  `supported_kinds`; W1 bumps the version and adds a `compact` capability.
- `src/aegis/web/history.py::read_history` — the disk read `get_event` reuses.
- `src/aegis/transcript_constants` — the constants block in `hello`.
- `src/aegis/web/wssession.py::SUPPORTED_KINDS` — event/state/inbox/
  session_list/queue_digest/history_complete/window_reset.
- Event kinds (codec type names): `AssistantText`, `AssistantThinking`,
  `ToolUse`, `ToolResult`, `AgentPlan`, `Result`, `SystemInit`.
- `src/aegis/telegram/` + `aegis serve` wiring in `src/aegis/cli.py` + the
  `telegram:` block in `src/aegis/config/` — the removal target (W6).

## W1 — Compact protocol v2

### Principle

Compaction is **field-level truncation of the existing encoded event**, not a
new schema. `decode_event` continues to accept the frame; heavy string fields
are clipped and flagged. This keeps one codec, one rendering path, and makes
the "full" fetch a straight disk read of the same shape.

### Frame changes to `event_frame`

New shape:

```
{type:"stream", kind:"event", handle, seq, event_type, event:<compacted>, truncated:<bool>}
```

- **`html` field removed.** Rendering moves fully client-side (W2). The
  server's `render_event_html` stays for the TUI but is no longer serialized
  onto the web wire.
- **`event`** is `encode_event(ev)` with heavy fields clipped per the table
  below.
- **`truncated`** is `true` when any field was clipped — the client shows a
  "tap to expand" affordance and enables `get_event` for that `seq`.

### Compaction contract by kind

| Kind | Compact wire (default) | Clipped? | On tap (`get_event`) |
|------|------------------------|----------|----------------------|
| `AssistantText` | **full text**, streamed (unchanged) — this is the answer | no | — |
| `AssistantThinking` | **stream suppressed**; on completion a marker with token count only | yes | full thinking text |
| `ToolUse` | tool name + first line of input | yes (if input > 1 line) | full input |
| `ToolResult` | first `TOOL_RESULT_HEAD_LINES` lines + total byte size | yes (if longer) | full output |
| `AgentPlan` | full (structured, short, high-value) | no | — |
| `Result` | full (metrics/cost, already tiny) | no | — |
| `SystemInit` | suppressed (already renders to nothing) | n/a | — |

Compaction thresholds are added to `src/aegis/transcript_constants` (single
source of truth, surfaced in `hello.constants`): `TOOL_RESULT_HEAD_LINES`,
`TOOL_INPUT_HEAD_LINES`, and a `THINKING_STREAM` boolean gate. Values chosen at
implementation time; defaults sized so a typical tool-heavy turn drops roughly
an order of magnitude in bytes.

### Thinking-stream suppression

`AssistantThinking` is usually the largest firehose and least useful live on a
phone. Under compact mode the per-chunk `AssistantThinking` frames are **not
emitted**; instead:

- while the agent is thinking, the existing `state` stream already carries the
  `working` state — the client shows a live "thinking…" indicator from that;
- on turn completion, a single compact `AssistantThinking` event frame carries
  the token count (no body), `truncated:true`.

The full thinking text remains on disk (JSONL) and is retrieved by `get_event`.
`AssistantText` chunks continue to stream normally.

### New RPC: `get_event`

Added to `WSSession._call`:

```
get_event  params:{handle, seq}  →  {event: <full un-truncated encode_event dict>}
```

Implementation reads the JSONL line at `seq` via `read_history` (or a
seq-indexed read helper alongside it) and returns the full `encode_event`
dict. Works identically for live-and-persisted and pure-history events, since
both live on disk by the time a user taps. The client caches the result keyed
by `(handle, seq)`; a second tap is instant, no round-trip.

### Version negotiation

- `PROTOCOL_VERSION` → `2`.
- `hello` gains `"capabilities": ["compact"]` (or extends `supported_kinds`);
  a client that understands compact frames renders truncation markers and wires
  `get_event`. A hypothetical older client (none ship) would see clipped bodies
  but still valid events. Since the web client and the future remote-TUI client
  are versioned together with the server, skew risk is low.

### History & resume under compact

`_open_session` history replay and `resume` gap-fill emit **compact** frames
(consistency: the transcript looks the same whether streamed live or replayed).
`get_event` covers expansion for any historical `seq`. No change to the
`resume` / `window_reset` / backpressure machinery.

### W1 tests

Extend the existing protocol tests (fake `SessionManager`, in-memory WS pair):

- compact `event` frame omits `html`, clips `ToolResult`/`ToolUse` bodies, sets
  `truncated`;
- `AssistantThinking` chunks suppressed; one compact marker on completion;
- `get_event(handle, seq)` returns the full un-truncated event; matches the
  JSONL line;
- history replay + resume emit compact frames; `get_event` expands a
  historical `seq`;
- `hello` reports `protocol_version:2` + the compact capability.

## W2 — Client-side rendering

With `html` gone, the JS transcript module renders each block from the compact
event.

- **Per-kind renderer registry** in `app.js` (or a new `render.js`) mirroring
  `render_html.py`'s per-kind logic: kind icon, plan glyphs, diff preview, tool
  header, result body, terminator/cost line. Reuses existing `markdown.js`
  (assistant text) and `coalesce.js` (streaming `(event_type, message_id)`
  aggregation, unchanged).
- **Tap-to-expand.** A block with `truncated:true` renders a compact body + an
  expand affordance. On tap: `get_event(handle, seq)` → replace the truncated
  body with the full render → cache. Collapsing is local (no refetch).
- **Parity gate:** the compact-then-expanded render must match the fidelity the
  server-`html` path produced for the same event (kind icons, diffs, plan
  blocks, cost line). A golden-fixture comparison against `render_event_html`
  output keeps the two renderers honest.

W2 tests: JS transcript module under node/jsdom — compact render per kind,
expand-on-tap swaps body, coalescing still merges streaming chunks, golden
parity against server HTML fixtures.

## W3 — PWA shell

- **`manifest.webmanifest`** — name, short_name, icons (maskable set),
  `display:"standalone"`, `theme_color`/`background_color` from the default
  theme, `start_url` preserving the token.
- **`service-worker.js`** — precache the app shell (`index.html`, CSS, JS,
  icons, manifest) on install; serve shell **cache-first** so the app launches
  instantly and works installed with no signal. WS traffic is never cached
  (it's live). A versioned cache name busts on deploy (`server_version`).
- **Transcript offline read.** The last-loaded transcript remains readable from
  the in-memory/DOM state after the wire drops; a "reconnecting…" banner shows
  when the WS is down. The existing `resume`/`last_seq` flow catches up on
  reconnect; backpressure-close → reconnect → resume already works.
- **Token persistence.** On first load, capture `?t=` into `localStorage`; the
  installed app reconnects without re-pasting. (Single shared secret, as today
  — per-device tokens remain out of scope.)
- **Explicitly excluded from v1:** an offline **outbox** that queues typed
  messages while disconnected and flushes on reconnect. Composing/sending
  requires a live connection in v1; the composer disables with a clear state
  when offline.

W3 tests: service-worker precache + cache-first shell fetch; offline shell
load; reconnecting-state transition on simulated WS drop.

## W4 — Mobile-first responsive layout + swipe

One DOM/state, two presentations split by a CSS breakpoint. Desktop is
unchanged (multi-tab bar, modal dashboards). Below the breakpoint:

- **List view (home / PWA launch screen).** Session rows: handle · agent ·
  state dot · unseen badge; a "＋ new agent" entry opening the agent picker.
  Driven by the existing `session_list` global stream — no new backend.
- **Conversation view (full-screen).** Compact transcript fills the screen;
  composer pinned to the bottom above the on-screen keyboard; back-button /
  back-swipe returns to the list. Tap any truncated block to expand (W2).
- **Swipe between agents.** A horizontal-swipe gesture handler maps left/right
  to prev/next handle in the `session_list` order (swipe = tab-switch). Reuses
  the same per-handle subscribe/unsubscribe the tab controller already does;
  swiping just changes which handle is foregrounded.
- **Dashboards hidden on mobile.** Queue/group/config/file surfaces are not
  laid out for mobile v1; they render an "open on desktop/TUI for this"
  affordance. Mobile is deliberately conversation-first.

W4 tests: responsive breakpoint renders list vs. conversation; swipe advances
foregrounded handle; composer stays above the keyboard; dashboards suppressed
below breakpoint.

## W5 — Web-default positioning

- `aegis web` (already present in `cli.py`: ensures a token, sets the port,
  opens the browser, serves) is documented as a **first-class launch verb**,
  co-equal with `aegis` (TUI).
- README / AGENTS.md / relevant docs reframed: **TUI for local dev, Web/PWA for
  remote-and-local dev — two co-equal first-class UIs.** The `2026-06-19`
  parent spec's "web is first-class desktop, mobile out of scope, Telegram
  retained" framing is superseded by this document.
- No behavior change to the `aegis` TUI in this slice. Full convergence on one
  backend is the separately-specced **TUI-as-WS-client** work
  (`2026-07-01-aegis-tui-ws-client-design.md`, S9–S10), which is a **downstream
  consumer** of this compact protocol, not part of this effort.

## W6 — Telegram removal (final slice)

Ships only after W1–W4 have been daily-driven from a phone as the proven
replacement. Then delete, as a bounded change nothing in core depends on:

- `src/aegis/telegram/` (the `BotClient`, `format.py`, `TelegramFrontend`).
- The `aegis serve` wiring that constructs `TelegramFrontend` in
  `src/aegis/cli.py` and the `AEGIS_TELEGRAM_TOKEN` handling.
- The `telegram:` config block + its schema/loader in `src/aegis/config/`
  (`load_telegram_config` and the dataclass), plus `aegis config` verbs that
  touch it.
- Its tests and docs (including AGENTS.md's `telegram/` layout entry).

**Explicitly out of scope of this removal:** the workspace's own
`notify-telegram.sh` / journal-prompt notification tooling — a separate system,
untouched. The inbox `sender_telegram` tag can remain a harmless no-producer
enum value or be removed if cleanly unreferenced; decide at implementation time
by grep.

## Slice breakdown & sequencing

| # | Slice | Deliverable |
|---|-------|-------------|
| **W1** | Compact protocol v2 | `event_frame` drops `html`, truncates heavy fields with `truncated` marker; thinking-stream suppression; `get_event` RPC; `protocol_version:2` + compact capability; compaction constants in `transcript_constants`. Protocol tests. |
| **W2** | Client-side rendering | JS per-kind renderer registry mirroring `render_html.py`; tap-to-expand → `get_event` + cache; drop server-`html` reliance; golden parity. |
| **W3** | PWA shell | manifest + service worker (precache shell, cache-first); install; token persistence; offline/reconnecting states. No offline outbox. |
| **W4** | Mobile layout + swipe | responsive breakpoint; list ↔ conversation; swipe-between-agents; composer-above-keyboard; dashboards hidden on mobile. |
| **W5** | Web-default positioning | `aegis web` as first-class verb; README/AGENTS/docs reframed to co-equal UIs. |
| **W6** | Telegram removal | delete frontend + serve wiring + config block + tests + docs (after W1–W4 daily-driven). |

**Order:** W1 → W2 strictly first (protocol then rendering). W3 and W4 build on
W2 and can partly parallelize. W5 anytime after W2. **W6 last.**

## Dependencies & downstream

- **Downstream:** `2026-07-01-aegis-tui-ws-client-design.md` (TUI as WS client,
  S9–S10) consumes this compact protocol automatically — the remote TUI over
  SSH gets the wire diet for free. That effort is not folded in here.
- **Superseded framing:** `2026-06-19-aegis-web-client-design.md` §"first-class
  desktop / mobile out of scope / Telegram retained" is superseded by this
  document's positioning.

## Risks

1. **Rendering parity drift.** Two renderers (server `render_html.py` for the
   TUI, JS for the web) can diverge. Mitigation: golden-fixture parity test in
   W2 comparing JS output to `render_event_html` per kind.
2. **Tap-to-expand latency on a flaky link.** `get_event` is a round-trip; on a
   bad connection the expand may stall. Mitigation: cache fetched detail;
   show a pending state; the compact summary is always already present, so the
   stall never blocks reading the stream.
3. **Thinking suppression hides useful signal.** Some users want live thinking.
   Mitigation: it's one tap away, and `THINKING_STREAM` is a constant gate — a
   future per-client preference can re-enable streaming without protocol
   change.
4. **PWA cache staleness after deploy.** Mitigation: versioned cache name keyed
   to `server_version`; the SW busts and re-precaches on version change.
5. **Removing Telegram before the PWA is truly sufficient.** Mitigation: W6 is
   gated on daily-driving W1–W4 from a phone first.

## Out of scope

- TUI-as-WS-client migration (separate spec, downstream consumer).
- Offline outbox (queue-and-flush composing while disconnected).
- Per-device / multi-user tokens (single shared secret remains).
- Mobile layouts for queue/group/config/file dashboards.
- Push notifications to the installed PWA (native web-push) — a natural future
  slice, not v1.
