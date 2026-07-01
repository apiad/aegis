# Aegis TUI as WS Client (S9–S10) — Design

**Status:** draft (spec only — not yet planned or implemented)
**Date:** 2026-07-01
**Scope:** make the Textual TUI a WebSocket client of `aegis serve`, so the
TUI, the web client, and Telegram are all thin frontends of one backend, with
sessions visible and continuable across all three. This is the final,
architectural slice of the web-client effort (parent spec:
`2026-06-19-aegis-web-client-design.md`, §"TUI migration").

## Why this is written now (and why it waited)

S1–S8 shipped the web client as a *second* frontend consuming the same
`SessionManager` observer seams Telegram uses — structurally identical to how
Telegram was added, and proven live in a browser. That work also produced the
thing S9 depends on: **a real, feature-tested WS protocol** (see
`2026-06-30-aegis-web-ws-protocol-design.md`) and a **reference JS client**
(`src/aegis/web/static/js/ws.js`). S9 is the inverse move — instead of adding a
frontend that reads the in-process manager, we make the *existing* TUI read a
*remote* manager over that same protocol. It waited because it refactors the
daily-driver TUI and moves the MCP plane out of the TUI process; both warrant
doing on a rested, deliberate pass rather than at the end of a long build.

## End state

- **`aegis serve`** is the only place session state lives (already true for the
  web + Telegram frontends). It owns `SessionManager`, the queue substrate, the
  inbox router, the scheduler, the groups/terminal/canvas planes, and the MCP
  plane.
- **`aegis`** (the TUI) runs as a WS client of `aegis serve` — auto-launching a
  co-resident serve on localhost if one isn't already running.
- A session spawned in any frontend appears in all of them; the TUI, web, and
  Telegram observe the same live transcripts.

## Architecture

### The lever: `RemoteSessionManager`

The TUI's `AegisApp` talks to a `manager` that today is an in-process
`SessionManager`. S9 introduces a `RemoteSessionManager` that implements the
**same `AppBridge` Protocol** (`src/aegis/mcp/bridge.py`) but whose methods are
WS RPC calls and whose observer wiring is WS subscriptions:

- `spawn(profile) -> handle` → `rpc("spawn_session", …)`
- `close(handle)` → `rpc("close_session", …)`
- `interrupt(handle)` → `rpc("interrupt_session", …)`
- deliver / send a user turn → `rpc("deliver", …)`
- `list_sessions()` / `list_agents()` → their RPCs (or the cached
  `session_list` stream)
- per-session `add_event_observer` / `add_state_observer` / `add_inbox_observer`
  → `subscribe(session)` + dispatch of the `event` / `state` / `inbox` stream
  frames into the same typed events the pane already renders.

Because the pane, renderer, theme engine, transcript windowing, and modals all
consume *typed events from somewhere*, swapping "somewhere" from
`session.add_event_observer(...)` to `ws.on("event", ...)` leaves the rendering
path unchanged. This is the parent spec's core claim, now concrete.

### `src/aegis/tui/ws_client.py`

A Python WS client mirroring `ws.js`: auth handshake, `rpc(method, params)` as
awaitable futures, `subscribe` / `resume`, reconnect-with-backoff, and dispatch
of stream frames. The `event`/`state`/`inbox` frames are decoded via the same
`aegis.state.event_codec` the web relies on, into the canonical
`aegis.events` types — so `pane.on_event` needs no change.

### What moves, what stays

**Moves out of the TUI process:** the MCP plane (`AegisMCP`) — it lives in
`aegis serve`, bound directly to the real `SessionManager`. The TUI no longer
co-hosts an MCP server (a structural cleanup).

**Stays identical in the TUI:** theme engine + `AegisColors`, all Textual
widgets (`ConversationPane`, `TabBar`, modals), transcript windowing
(`_history`, sticky-bottom, eviction, scroll-up restoration, streaming
aggregation), render dispatch, keyboard shortcuts, file picker/viewer, config
panel. Plugins extend agent behavior, not the TUI, so they are unaffected.

**New in the TUI:** startup `session_list` sync (today's TUI starts with no
sessions; the remote TUI subscribes to `session_list` on connect and shows any
already-running sessions as tabs) and reconnection logic (same `last_seq` /
`resume` flow as the web client).

## Prerequisite: WS protocol coverage for full TUI parity

The web client drives a *subset* of the `AppBridge`/`SessionManager` surface.
The TUI drives more. Before the TUI can run fully remote, the WS protocol must
cover everything the TUI's tabs and modals invoke. Audit of the gap (as of S8):

**Already covered** (the core conversation loop — the bulk of daily use):
- spawn / close / interrupt / deliver
- `event` / `state` / `inbox` streams + `session_list`
- queue digest (`queue_digest` + `queue_tail`), group *status*, file
  search/read, config show + edit, theme list.

**Missing — needed for full parity** (each is a small, additive RPC/stream on
the existing `WSSession` dispatch, mirroring the patterns S2a–S8 established):
- `handoff(from, target, context)` — the TUI's handoff action.
- `rename_handle(old, new)` — rename a tab.
- **Group *operations*** beyond status: `group_spawn`, `group_broadcast`,
  `group_wait_all/any`, `dissolve`, `move_member` — the TUI groups dashboard
  can act, not just view. (Web only reads.)
- **Terminals** — `sys_terminal_*` equivalents: spawn/run/keys/read/close +
  a `term:<name>` stream. The TUI has live shared PTYs.
- **Canvas** — open/read/write_section/append + a `canvas:<name>` stream.
- **Workflow** invocation — `run_workflow` + its progress messages.
- **Scheduler** views the TUI surfaces (list/logs), if any.

Decision to make at plan time: **stage the migration by surface**. S9a can run
the *conversation panes* remotely (everything under "already covered") while
the auxiliary dashboards (terminals, canvas, group ops) either (a) get their
RPCs added first, or (b) remain temporarily disabled under `--remote` until
their protocol coverage lands. A partial `--remote` that runs conversations
remotely but greys out terminals is an honest, shippable intermediate.

## Migration path (safety-first)

Mirrors the parent spec's S9→S10:

1. **S9 — opt-in `--remote`.** `aegis --remote` runs the TUI with
   `RemoteSessionManager` in place of the in-process one; auto-launches a
   co-resident `aegis serve` on localhost if not already running (auto-token,
   never leaves the machine — see the parent spec's "Zero-config local UX
   preservation"). Classic in-process TUI stays the **default**. `--remote` is
   opt-in for daily-driving and testing.
2. **S10 — flip the default** after ≥1 week of daily-driving `--remote` without
   regressions. `--classic` retained for one release as a safety valve, then
   the in-process path is deleted.

**Zero-config guardrail:** typing `aegis` and getting a TUI must still require
zero setup. `--remote` (and later the default) auto-launches the daemon;
`aegis.serve.lifecycle: tui_owned | persistent` controls whether it disowns on
exit or stays running.

## Risks

1. **It touches the working TUI.** Mitigation: `--remote` opt-in with
   `--classic` always available; flip only after a week of daily use.
2. **Protocol gap (above).** Full parity needs the missing RPCs/streams; a
   staged, surface-by-surface migration de-risks this and keeps each step
   shippable.
3. **MCP plane relocation.** Moving `AegisMCP` out of the TUI process into
   serve is a real change to how agents reach the substrate; verify the
   co-resident serve's MCP is what spawned agents connect to.
4. **Two-process debugging.** "Client, server, or wire?" becomes a question;
   the JSONL substrate + per-frame logging (already in place for the web)
   carries over.
5. **Reconnection during a live turn.** The web client's `resume` flow is
   proven; the Python client reuses the same `last_seq` contract.

## Testing strategy

- **`tests/test_remote_tui_parity.py`** — drive `RemoteSessionManager` through
  the same WS protocol the web protocol tests use (`test_web_protocol.py`),
  asserting both clients drive the same operations to the same outcomes.
- **Python WS client unit tests** — auth, rpc futures, subscribe/resume,
  reconnect, mirroring `tests/web/` coverage on the JS side.
- **Live** (`live` marker) — `aegis --remote` against a real co-resident serve
  + a real `claude`: spawn → stream → interrupt → close → reopen for resume;
  and a cross-frontend check (spawn in web, see it in the `--remote` TUI).

## Slice breakdown (proposed)

| # | Slice | Deliverable | ~Effort |
|---|-------|-------------|---------|
| **S9.0** | Protocol parity for the conversation loop | Confirm/close any gaps in spawn/close/interrupt/deliver/handoff/rename + the three streams so a pane can run fully remote. Add `handoff` + `rename_handle` RPCs. | ~0.5d |
| **S9.1** | `ws_client.py` | Python WS client mirroring `ws.js` (auth/rpc/subscribe/resume/reconnect), decoding frames into `aegis.events`. Unit-tested. | ~1d |
| **S9.2** | `RemoteSessionManager` | Implements `AppBridge`; methods → RPC, observers → subscriptions. `aegis --remote` swaps it in; auto-launches co-resident serve; `session_list` startup sync. Conversation panes work end-to-end; auxiliary dashboards greyed if their RPCs aren't in yet. | ~1.5d |
| **S9.3** | Auxiliary-surface RPCs (as needed) | Add group ops / terminals / canvas / workflow RPCs + streams to reach full TUI parity under `--remote`. Can be incremental. | ~1–2d |
| **S10** | Flip default + cleanup | `--remote` becomes default after a week of daily use; `--classic` fallback for one release; delete the in-process path after. | ~0.5d |

## References

- WS protocol: `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`
- Parent web-client design (§"TUI migration"):
  `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
- Reference JS client: `src/aegis/web/static/js/ws.js`
- Backend surface: `src/aegis/mcp/bridge.py` (`AppBridge`),
  `src/aegis/core/manager.py` (`SessionManager`),
  `src/aegis/web/wssession.py` (the server-side protocol handler to extend).
