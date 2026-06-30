# Aegis Web Client — WebSocket Protocol Design

**Status:** draft
**Date:** 2026-06-30
**Scope:** the wire contract between the browser client and `aegis serve`'s
new `WebFrontend`. This is the sub-spec the parent web-client design
(`2026-06-19-aegis-web-client-design.md`, §"WebSocket protocol") asked to be
written *before* the S2 plan, so the contract is explicit and reviewable.
Grounded against the real backend surface (`SessionManager`, `AgentSession`
observers, `event_codec`, `session_log`) rather than the parent spec's
aspirational names.

## Why a separate spec

S2 is the parent plan's "dragon" — the first convergence of protocol, JSONL
history reader, FastAPI integration, transcript JS, event rendering, theme
rendering, auth, and reconnection. Pinning the wire contract first lets the
S2 plan reference a fixed surface, and lets the protocol be reviewed
independently of the implementation.

## Grounding corrections to the parent spec

Three facts about the real backend change the protocol's shape. They
supersede the corresponding wording in the parent spec.

1. **The session identifier is the `handle`, not a separate `session_id`.**
   aegis identifies every live session by its handle (e.g. `swift-bohr`).
   `SessionManager.spawn(profile) -> handle`, `list_sessions() ->
   list[SessionInfo]` where `SessionInfo.handle` is the key, and persistence
   is `<state_dir>/sessions/<handle>.jsonl`. The protocol uses `handle`
   everywhere the parent spec said `session_id`.
2. **No per-line `seq` on disk** (from the S1 persistence audit). Each JSONL
   line is `{"v": 1, "aegis_ts": <iso>, "event": <encoded>}`. `seq` is
   *synthesized* as the 1-based line index on read. Live events emitted after
   the last persisted line continue the counter in memory.
3. **`replay_events` is not torn-line tolerant** (S1 audit). The history
   reader this protocol depends on (§History reader) must drop an
   unparseable trailing line rather than raise.

## Real backend surface this protocol binds to

From `src/aegis/`:

- `core/manager.py::SessionManager` — `async spawn(profile, *, handle=None)
  -> str`, `async close(handle)`, `async interrupt(handle)`,
  `get(handle) -> AgentSession | None`, `list_sessions() -> list[SessionInfo]`,
  `list_agents() -> list[str]`, `live_handles() -> set[str]`,
  `async handoff(from_handle, target_handle, context)`.
- `core/session.py::AgentSession` — per-session observer seams
  `add_event_observer(cb)`, `add_state_observer(cb)`, `add_inbox_observer(cb)`,
  `add_close_observer(cb)`; `async deliver(msg: InboxMessage) -> Delivery`;
  `state: AgentState`.
- `mcp/bridge.py::SessionInfo` — `handle, agent_slug, state, active, unseen`.
- `state/event_codec.py` — `encode_event(ev) -> dict`, `decode_event(d) -> Event`.
- `state/session_log.py` — `session_log_path(state_dir, handle)`,
  `replay_events(...)`. The torn-line-tolerant reader is added alongside (§History reader).
- `queue/...` helpers — `sender_user(text) -> InboxMessage` (the headerless
  plain-user-turn envelope the TUI text box uses).
- `render_html.py::render_event_html(ev) -> str | None` (S1).
- `telegram/frontend.py::TelegramFrontend._attach_observers` — the reference
  pattern: a second frontend attaches the three observers per spawned session.

## Connection model

One WebSocket per browser window/tab. That single connection multiplexes
events for every conversation-tab open in that window plus global streams
(`session_list`). Mirrors the TUI process: one event loop, N pane
subscriptions. A `WSSession` object owns the socket; a `SubscriptionRegistry`
fans one per-handle observer out to all `WSSession`s subscribed to that handle
(so N windows watching one agent share a single set of backend observers).

## Framing

All frames are JSON with a `type` discriminator.

| Direction | `type` | Purpose |
|-----------|--------|---------|
| C → S | `auth` | First frame after upgrade; carries the token |
| C → S | `rpc` | Request with `id`, awaits `rpc_response` |
| C → S | `subscribe` / `unsubscribe` | Toggle per-handle or global stream membership |
| C → S | `resume` | Reconnect with per-subscription `last_seq` |
| S → C | `hello` | Post-auth handshake: versions + constants |
| S → C | `rpc_response` | Response keyed by request `id` |
| S → C | `stream` | Server-pushed event, discriminated by `kind` |
| S → C | `error` | Protocol-level error (bad frame, unknown rpc) |

## Auth handshake

1. WS upgrade carries `?t=<token>` (browsers can't set headers on WS open).
2. First client frame: `{type: "auth", token: "<token>"}`.
3. Server validates against the configured `web.token`. On success it replies:

```json
{
  "type": "hello",
  "server_version": "0.16.0",
  "protocol_version": 1,
  "constants": {
    "N_MAX": 300, "EVICT_BATCH": 50, "LOAD_BATCH": 100,
    "STICKY_EPS": 2, "LOAD_MORE_EPS": 3, "DEBOUNCE_S": 0.15,
    "RESUME_GAP_CAP": 1000
  },
  "supported_kinds": ["event", "state", "inbox", "session_list",
                      "history_complete", "window_reset"]
}
```

   `constants` is sourced from `aegis.transcript_constants` (S1) plus
   `RESUME_GAP_CAP` — single source of truth, client uses server-supplied
   values.
4. Invalid token or no `auth` frame within 5s → server closes with code
   `4401`.

`protocol_version` is an integer. Client refuses to operate if
`server_version`'s protocol major exceeds what it was built for (banner asks
for refresh). Minor/forward-compat skew is allowed; event-shape evolution
rides the codec's existing legacy-record decode.

## RPC surface (S2 subset)

Request: `{type: "rpc", id: <int>, method: "<name>", params: {...}}`.
Response: `{type: "rpc_response", id: <int>, ok: true, result: {...}}` or
`{type: "rpc_response", id: <int>, ok: false, error: "<message>"}`.

S2 ships exactly these — each maps to one real backend call:

| Method | Params | Maps to | Returns |
|--------|--------|---------|---------|
| `list_agents` | — | `manager.list_agents()` | `{agents: [str, ...]}` |
| `list_sessions` | — | `manager.list_sessions()` | `{sessions: [{handle, agent_slug, state, active, unseen}, ...]}` |
| `spawn_session` | `{agent_profile}` | `manager.spawn(agent_profile)` | `{handle}` |
| `close_session` | `{handle}` | `manager.close(handle)` | `{ok: true}` |
| `interrupt_session` | `{handle}` | `manager.interrupt(handle)` | `{ok: true}` |
| `deliver` | `{handle, message}` | `manager.get(handle).deliver(sender_user(message))` | `{delivery: "landed"\|"queued", depth: <int>}` |

Later slices extend this surface (`handoff`, `enqueue`, queue/group/file
introspection) — out of scope here. `deliver`'s return mirrors the
`Delivery(landed|queued, depth)` receipt `AgentSession.deliver` already
produces, so the web input box can show the same pending-chip behavior as the
TUI.

## Stream messages (server → client)

`{type: "stream", kind: "<k>", ...}`. S2 kinds:

```
{type:"stream", kind:"event",            handle, seq, event_type, event, html}
{type:"stream", kind:"state",            handle, state, metrics}
{type:"stream", kind:"inbox",            handle, seq, msg}
{type:"stream", kind:"session_list",     added:[...], removed:[...], updated:[...]}
{type:"stream", kind:"history_complete", handle, current_seq}
{type:"stream", kind:"window_reset",     handle, dropped_through_seq}
```

- **`event`** — one rendered transcript event.
  - `event_type`: canonical codec type name (`AssistantText`,
    `AssistantThinking`, `ToolUse`, `ToolResult`, `AgentPlan`, `Result`,
    `SystemInit`, …).
  - `event`: the `encode_event(ev)` dict — the client's source of truth for
    events that need client-side aggregation (streaming text/thinking chunks)
    and for copy-to-clipboard payload.
  - `html`: `render_event_html(ev)` output (S1). Present for unit blocks
    (tool use/result, plan, result separator). `null` when the renderer
    returns `None` (e.g. `SystemInit`) — the client skips mounting.
  - Streaming `AssistantText`/`AssistantThinking` chunks are pushed as
    individual `event` frames carrying `event` (raw chunk); the client
    coalesces by `(event_type, message_id)` mirroring
    `aegis.render.coalesce_chunks`, and may ignore `html` for these.
- **`state`** — `state` is the `AgentState.value` string
  (`ready|working|error`); `metrics` is the serialized `SessionMetrics`
  (tokens, cost, turn count) the status bar renders.
- **`inbox`** — an incoming `InboxMessage` (handoff/queue callback/etc.),
  serialized; the client renders it with the inbox block styling.
- **`session_list`** — global stream; `added`/`removed`/`updated` carry
  `SessionInfo` dicts. Pushed when any session is spawned/closed/changes
  state so every window's TabBar stays coherent.
- **`history_complete`** / **`window_reset`** — see §History & resume.

## Subscriptions

```
{type:"subscribe",   target:{kind:"session", handle:"swift-bohr"}}
{type:"subscribe",   target:{kind:"global",  stream:"session_list"}}
{type:"unsubscribe", target:{...}}
```

On `subscribe(kind:"session", handle)`:

1. Server reads the full JSONL for `handle` via the history reader, assigning
   each line `seq = 1..N`.
2. Streams each as a `stream/event` frame (with `html` pre-rendered).
3. Sends `{type:"stream", kind:"history_complete", handle, current_seq:N}`.
4. Live events from the in-memory observer continue at `seq = N+1, N+2, …`.

The `SubscriptionRegistry` attaches one set of `add_event_observer` /
`add_state_observer` / `add_inbox_observer` callbacks per handle (lazily, on
first subscriber) and removes them when the last subscriber leaves — the same
attach pattern as `TelegramFrontend._attach_observers`, but reference-counted
across `WSSession`s.

## History reader

New: `src/aegis/web/history.py::read_history(state_dir, handle) ->
list[tuple[int, Event]]`. Wraps `session_log` reads but, per the S1 audit:

- Synthesizes `seq` as the 1-based line index.
- Wraps each line's `json.loads` + `decode_event` in try/except; an
  unparseable **trailing** line is dropped (torn write), while a malformed
  **interior** line raises (genuine corruption) — mirroring the
  torn-trailing-line tolerance in `groups/persistence.py`.
- Returns `[]` when the file is absent.

This is the read path for both `subscribe` (full history) and `resume`
(gap fill). No in-memory ring buffer in v1.

## Reconnection & resume

- Each `WSSession` (client side) tracks the highest `seq` received per
  subscribed handle.
- On reconnect, after `auth`/`hello`, the client's first frame is:

```json
{"type":"resume",
 "subscriptions":[{"handle":"swift-bohr","last_seq":412}, ...],
 "globals":["session_list"]}
```

- Server behavior per subscription:
  - **Small gap** (`current_seq - last_seq <= RESUME_GAP_CAP`): stream only
    `last_seq+1 .. current_seq`, then live events resume.
  - **Large gap or first-ever**: treat as a fresh subscribe — send
    `{type:"stream", kind:"window_reset", handle, dropped_through_seq:last_seq}`
    so the client discards its stale tail, then stream full history as above.
- The session never pauses during a disconnect — agents keep running and
  events keep flushing to JSONL; the gap is filled from disk on resume.
- Server pings every 30s; client treats ≥60s silence as disconnect and
  reconnects with backoff.

## Backpressure

Per-`WSSession` send queue capped (default 10k frames). If a client falls
behind, the server closes the WS with reason `backpressure`; the client
reconnects and `resume`s, catching up cleanly from JSONL. Durable events are
never dropped silently — persistence is the source of truth; the WS is only a
delivery channel.

## Error frames

`{type:"error", code:"<slug>", message:"<text>", id:<int?>}`. Codes:
`bad_frame` (unparseable/typeless), `unauthorized` (pre-`hello` non-auth
frame), `unknown_method`, `unknown_target`. When the error answers a specific
`rpc`, `id` echoes it; otherwise `id` is absent.

## Out of scope (this spec)

- Queue/group/file/config RPC + their stream kinds (`queue_digest`,
  `group_state`, etc.) — added per their slices (S4–S6).
- The JS transcript module's internal structure — covered by the parent
  spec's §"Client-side composition"; this spec only fixes the wire it reads.
- Multi-user / per-device tokens — single shared secret in v1.
- In-memory ring buffer — JSONL reads suffice for v1.

## Test contract

The S2 plan tests this protocol against a **fake `SessionManager`** over an
in-memory WS pair (no real harness subprocess):

- `auth` success → `hello` with the constants block; bad token → close 4401.
- `subscribe(session)` → full history with synthesized `seq` 1..N →
  `history_complete{current_seq:N}` → a live event arrives at `seq N+1`.
- `resume` small gap → only the missing tail streams.
- `resume` large gap / first-ever → `window_reset` then full history.
- `deliver` rpc → fake manager records the `sender_user` message; response
  carries the `Delivery` shape.
- `history.read_history` torn trailing line → dropped, no raise; interior
  corruption → raises.
- Backpressure: overflowing the send queue closes with `backpressure`.

These are the acceptance gates the S2 plan's "WS protocol" task will encode.
