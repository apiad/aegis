# aegis — unified input queue + click-to-dequeue user chips

**Date:** 2026-06-26
**Status:** designed

## Problem

Two inbound paths behave inconsistently when an agent is mid-turn:

- **User text-box input** is *blocked*: `on_growing_input_submitted` early-returns
  when `state is working`, and `_submit` disables the input for the turn. You
  cannot line up a follow-up while the agent runs.
- **Agent handoffs** (`aegis_handoff`) to a busy peer are *rejected* —
  `"handoff rejected: <target> is busy, retry shortly"`.

Meanwhile the inbox already buffers handoffs/callbacks/telegram cleanly
(`AgentSession._inbox_buffer` + `_chain_if_pending`) when they arrive mid-turn.

We want **one** queue for everything inbound, a delivery receipt that tells the
sender whether the message **landed** (consumed immediately) or **queued**
(buffered behind an in-flight turn), and a visible queue of pending *user*
messages — rendered as chips above the input line — that can be **dequeued with
a click** before they reach the agent.

## Goals

1. User text-box input and agent handoffs flow through one queue; both buffer
   while the agent is working and drain at the turn boundary (existing inbox
   chaining).
2. The sender of a handoff gets a **landed vs queued** receipt as the return
   value. Busy handoffs stop being rejected — they queue.
3. Queued user messages render as clickable chips **directly above the input
   box**; clicking a chip **cancels** that message so it never reaches the
   agent.
4. The agent's view of a plain text-box message is unchanged — it arrives as a
   normal user turn, with no substrate `> from …` header.

## Non-goals

- Chips for queued **non-user** messages (handoffs/telegram). Those already
  render as transcript blocks on arrival; no chip, no dequeue. (YAGNI.)
- Changing `aegis_enqueue` semantics. Enqueue targets a worker *queue* (a
  separate plane) and already returns `queued_position`; its workers are not a
  live session, so "landed" has no meaning there.
- Web client UI. The queue *model* lives in core (frontend-agnostic), so the
  future WS client (`2026-06-19-aegis-web-client-design.md`) inherits it; only
  the chip *UI* is built here, in the Textual TUI.
- Editing or reordering queued messages. Click = cancel only.

## Design

### Unit 1 — Core: one queue, with a delivery receipt

`AgentSession.deliver(msg)` is already the single buffering path. Change it to
**return a receipt** and to **announce dispatch**.

New value type (in `queue/schema.py`, beside `InboxMessage`):

```python
@dataclass(frozen=True)
class Delivery:
    disposition: str   # "landed" | "queued"
    depth: int         # queue position; 0 when landed
```

`deliver(msg) -> Delivery`:

- **idle** (`state is not working`): drains the buffer and starts the turn now
  (unchanged behavior) → returns `Delivery("landed", 0)`.
- **working**: appends to `_inbox_buffer` (unchanged) → returns
  `Delivery("queued", len(self._inbox_buffer))`. `depth` is the message's
  1-based position in the buffer after append.

New observer, symmetric with the existing `on_event`/`on_state`/`on_inbox`
seams (primary slot + `add_dispatch_observer` extra list):

```python
OnDispatchCb = Callable[[AgentSession, list[InboxMessage]], None]
self.on_dispatch: OnDispatchCb | None = None
```

`on_dispatch(self, batch)` fires **the instant a batch leaves the buffer to
start a turn**, in both places that drain it: the idle branch of `deliver()`
and `_chain_if_pending()`. The batch is the exact list of `InboxMessage`s being
sent. (It does *not* fire for the plain `send(text)` path — that path stays for
programmatic/test callers; the TUI no longer uses it.)

New method:

```python
def cancel_pending(self, msg: InboxMessage) -> bool:
    """Remove a still-buffered message by object identity.
    Returns True if removed, False if already dispatched/absent."""
```

Removal is by `is` identity — the frontend holds the exact object it created,
so no id field is needed on the (frozen) `InboxMessage`.

`send(text)` is unchanged (kept for `send_and_wait`, `SessionManager`, and any
programmatic caller). The idle-drain and chain logic is unchanged except for
the added `on_dispatch` fire and the `Delivery` return.

### Unit 2 — Schema: a `user` sender that renders headerless

```python
def sender_user() -> str:
    return "user"
```

`render_inbox_header(msg)` returns `""` when `msg.sender == "user"`.
`AgentSession._render_batch` omits the header line when the rendered header is
empty (joins just the body). Result: a text-box message reaches the harness as
plain user text — identical to today — while a handoff keeps its
`> from agent:<handle> · <ts>` header. A mixed batch (user message + handoff
buffered in the same turn) renders the user body first (headerless) then the
handoff with its header, joined by the existing blank-line separator.

### Unit 3 — MCP: handoffs queue instead of rejecting

`aegis_handoff(from_handle, target_handle, context)`:

- Keep the self-handoff and unknown-target rejections.
- **Remove** the `target_info.state == "working"` busy-reject.
- Deliver through `bridge.inbox_router.deliver(...)`, which now returns a
  `Delivery`, and return a string:
  - `"landed at <target>"` when `disposition == "landed"`.
  - `"queued for <target> (position <depth>)"` when `"queued"`.

`InboxRouter.deliver(handle, msg) -> Delivery`: returns the `Delivery` from the
live session's `deliver`, or — when no session is bound — appends to
`_pending` and returns `Delivery("queued", len(self._pending[handle]))`. The
existing callers (`groups/runtime.py`, `workflow/runner.py`,
`mcp/server.py` enqueue-callback at line ~922) ignore the new return value;
adding it is backward-compatible.

The `aegis_handoff` docstring and the `aegis_meta` briefing line are updated to
describe queue-on-busy + the landed/queued return.

### Unit 4 — TUI: chips above the input, click to dequeue

**Submit path** (`on_growing_input_submitted` / `_submit`):

- Drop the `state is working` early-return. **Stop disabling the input** during
  a turn (so you can keep typing/queuing).
- Build `InboxMessage(sender=sender_user(), timestamp=now_iso(), body=text)`,
  clear the input, and `receipt = await self._core.deliver(msg)`.
  - `landed` → start the working indicator; the user line is mounted by the
    `on_dispatch` handler (single render site).
  - `queued` → add a chip to the `PendingStrip` carrying the `msg` object.

**New widgets** (`tui/widgets.py`):

- `Chip` — one clickable widget showing truncated message text (e.g. first
  ~40 chars + `…`). Emits a `Chip.Dequeued(msg)` message on click (Textual
  `on_click` / `@on`). Styled from the palette like the existing `QueueStrip`.
- `PendingStrip` — a horizontal container of `Chip`s, mounted between the
  `StatusBar` and the `GrowingInput` in `ConversationPane.compose`. Hidden
  (0-height) when empty. Methods: `add(msg)`, `remove(msg)`, `clear()`,
  `set_palette(...)`. Follows the `QueueStrip` precedent (palette threading via
  `set_palette`, already iterated in `ConversationPane.set_palette`).

**Dequeue** (pane handles `Chip.Dequeued`): `self._core.cancel_pending(msg)`;
remove the chip from the strip. If `cancel_pending` returns False the message
already dispatched — the chip was already cleared by `on_dispatch`, so this is a
no-op in practice.

**Dispatch** (`_on_core_dispatch`, registered via `add_dispatch_observer`):
for each `msg` in `batch` with `sender == "user"`: remove its chip from the
strip (if present) and mount it as a normal user line
(`render_user_line`). Non-user messages are left alone — they were already
rendered by `_on_core_inbox` at arrival.

**`_on_core_inbox`** now skips `sender == "user"` (user messages are owned by
the chip/dispatch flow); it keeps rendering agent/queue/telegram arrivals as
transcript blocks exactly as today.

### Data flow

Idle agent, user types "X":
`submit → deliver(X) → on_dispatch([X]) [pane mounts user line] → Delivery("landed",0) → indicator on`.

Working agent, user types "Y":
`submit → deliver(Y) → buffer=[Y] → Delivery("queued",1) → chip(Y)`.
Turn ends → `_chain_if_pending` drains `[Y]` → `on_dispatch([Y])` [pane removes
chip Y, mounts user line Y] → new turn runs.

Working agent, user types "Z" then clicks chip Z:
`submit → buffer=[Z] → chip(Z)`; `click → cancel_pending(Z) → buffer=[] → chip
removed`. Z never reaches the agent.

Busy peer handoff:
`aegis_handoff → inbox_router.deliver → session.deliver → buffer append →
Delivery("queued",N) → return "queued for <target> (position N)"`. At the
target's turn end the handoff drains with its `> from agent:…` header
(on_dispatch skips it; it was rendered by on_inbox on arrival).

### Error handling

- `cancel_pending` on an already-dispatched/absent message → returns False, pane
  no-ops. No exception.
- `on_dispatch` observers are wrapped like the other observer fires
  (exceptions logged, not propagated).
- A chip click after the strip is torn down (pane closing) is a normal Textual
  widget lifecycle no-op.

## Testing

**Core (`tests/test_session_*.py`):**
- `deliver` returns `landed` (depth 0) when idle, `queued` (depth = buffer
  length) when working.
- `on_dispatch` fires with the exact batch on idle-drain and on chain-drain;
  does not fire for `send(text)`.
- `cancel_pending` removes by identity; returns False for an unknown/dispatched
  message; a cancelled message is absent from the next dispatched batch.

**Schema:**
- `render_inbox_header` returns `""` for `sender == "user"`.
- `_render_batch` of `[user, handoff]` yields body-then-headered-handoff.

**MCP (`tests/test_mcp_*.py`):**
- handoff to a busy target → message buffered, returns
  `"queued for <t> (position N)"`.
- handoff to an idle target → returns `"landed at <t>"`.
- self / unknown rejections unchanged.

**TUI (`tests/test_pane_*.py`, following existing widget-test patterns):**
- submit while working adds a chip; chip text is the truncated body.
- `Chip.Dequeued` removes the chip and the message never appears in a dispatched
  batch.
- `on_dispatch` for a user batch clears the chip and mounts a user line.
- input is no longer disabled during a turn.

Run with `uv run pytest -q -m "not live"`.

## Files touched

- `src/aegis/queue/schema.py` — `Delivery`, `sender_user`, header change.
- `src/aegis/core/session.py` — `deliver` return, `on_dispatch` seam,
  `cancel_pending`, `_render_batch` headerless join.
- `src/aegis/queue/inbox.py` — `deliver` returns `Delivery`.
- `src/aegis/mcp/server.py` — handoff queue-on-busy + receipt string; docstring
  + `aegis_meta` briefing line.
- `src/aegis/tui/widgets.py` — `Chip`, `PendingStrip`.
- `src/aegis/tui/pane.py` — submit path, dispatch handler, inbox skip,
  compose + palette threading.
- Tests as above.
