# Turn attention: the recap says whether a turn needs you

> **Status:** implemented 2026-09-17, per
> `docs/superpowers/plans/2026-09-17-aegis-turn-attention.md`. Designed with
> Alex through browser mockups of both screens. Builds on the every-turn recap (3c2a99a) and feeds
> `2026-09-17-aegis-fleet-dashboard-v2-design.md`. The approved mockup is
> `.playground/fleet-render/.superpowers/brainstorm/1761831-1789648257/content/attention-both-screens.html`
> (workspace playground, not in this repo).

## Why

The recap says what a turn did. With several tabs open, the question Alex
actually has is which of them is waiting on him, which broke, and which can
wait until he has time. Today the only per-tab signal is the state dot
(working or ready) and the unread `*`, and neither separates a session that
asked him a question from one that finished and has nothing to say.

The recap already runs after every turn and already returns structured output,
so classifying the turn costs one more field in a call that is paid anyway.

## The categories

Every finished turn gets exactly one attention category.

| category | meaning | urgency | mark |
|---|---|---|---|
| `needs_input` | the turn ended on a question or a decision for the operator | high | `?` on an accent block, blinking where it is not the active tab |
| `error` | something failed: tests red, a command broke, the harness errored | high | `✗` on an error-coloured block |
| `review` | the turn presents something for the operator to read (a spec, a plan, a report, a diff) without being blocked on it | medium | `◆` bold ink |
| `waiting` | the session waits on something that is not the operator: a monitor, a queue callback, a subagent or workflow it launched | none | `⧗` muted |
| `done` | informs that something finished or moved; nothing to do | low | `✓` ready colour |

Only the current palette is used; no theme gains a colour. Orange already
means working, so `needs_input` is told apart by the filled block and the
glyph, not by hue.

## How a turn is classified

The model proposes, hard signals decide.

1. **The model.** `TurnRecap` gains `attention: Literal["needs_input", "error",
   "review", "waiting", "done"]`, with a field description that defines each
   in one sentence. The system prompt tells it to prefer the FACTS block and
   the last assistant message, and that a question to the operator is
   `needs_input` even when the turn also landed work.
2. **Hard signals override it**, in this order:
   - the turn's `Result.is_error`, a harness error, or no `Result` at all →
     `error`;
   - the session is ephemeral (`origin.ephemeral`: queue, workflow or group
     worker) → never `needs_input`; it becomes `done`, since its answer goes to
     whoever made it;
   - at the moment the recap lands, the session has a live monitor, is owed a
     queue callback, or has a live session it spawned (`spawned_by`) that is
     still working → `waiting`, unless the result is already `error` or
     `needs_input`.
3. **A recap that fails** (refused, errored, no line) still sets the category
   from the hard signals alone, defaulting to `done`, so a missing recap never
   leaves a stale `needs_input` behind.

`waiting` is also re-derived live: when the last wait ends (the monitor
finishes, the callback arrives) and no new turn has started, the category
drops to `done`. In practice the callback starts a turn, whose recap replaces
it.

## Where it lives

- `AgentSession.attention: str` and `attention_seq: int`, set when a turn's
  classification lands; `attention_seq` increases each time. Both live on the
  brain's session, so every view and F10 read the same value.
- `RecapNote` gains `attention: str = "done"`, so the category survives a
  restart through `rehydrate_card`, exactly as the line does. An old record
  without the field decodes as `done`.
- **Seen is per view**, like `unseen` today. A `ConversationPane` keeps
  `attention_acked: int`; opening the tab (the moment `unseen` is cleared)
  sets it to the session's `attention_seq`. A category is *pending* in a view
  while `attention_seq > attention_acked` and the category is not `waiting`.
  `waiting` is shown while it holds, whether seen or not.

## What each screen shows

### The main screen

- **Tab bar** (`widgets.py` `render_tab`). The leading mark is:
  - the pulsing state dot while the session is working, as today;
  - otherwise the category's mark while it is pending (or `waiting`);
  - otherwise the plain ready dot, as today.
  The `?` block blinks only on tabs that are not active. The unread `*` stays.
- **The recap block in the transcript** (`render.render_recap`). The header
  reads `recap · <mark> <label>` (`needs you`, `error`, `review`, `waiting`,
  `done`) and the block's left edge takes the category colour. Body and footer
  are unchanged.
- **When a recap is drawn.** Any category other than `done` is drawn. `done`
  is drawn only when the turn moved the substrate, which is today's rule. A
  question with no file written is exactly the turn that must be drawn.
- **The F3 sidebar**, SESSION section: the state line appends the pending
  category (`idle · ? needs you`).

### F10 (v2 design)

- **Band row 3** counts by category instead of by state: working, need you,
  error, review, waiting, done. Disjoint and summing to the total: a working
  session counts as working whatever its last category; a ready session with
  no pending category counts as done.
- **List item**: the category mark leads the item and its label replaces the
  state word on the right (`needs you · 6m`). Tab order is kept.
- **Detail header**: the same mark and label.
- **Rotation priority** (v2 spec): `needs_input`, then `error`, then a monitor
  that finished, then `review`, then a new `did` or plan movement, then a state
  change, then a new `now`.
- F10 reads pending-ness through the view it is opened in, so opening a tab
  clears its mark in F10 too.

## Cost

No extra call. One enum field adds a few output tokens to a recap that costs
$0.004-0.015 on haiku.

## Testing

- Schema and prompt: a recorded question turn, error turn, review turn and
  plain commit turn, through `recap_turn` with a fake driver returning each
  category, reach the session unchanged.
- Hard signals, each with a fake clock and brain: an error `Result` overrides a
  model `done`; an ephemeral worker's `needs_input` becomes `done`; a live
  monitor turns `done` into `waiting` and leaves `needs_input` alone; a failed
  recap still sets `error` from the Result.
- Persistence: `RecapNote(attention=...)` round-trips; an old record decodes as
  `done`; `rehydrate_card` restores the category.
- Seen: opening the tab acks it; a second view still shows it pending; a new
  turn's category shows again; `waiting` shows while acked.
- Render: `render_tab` for each category, active and not, frame 0 and 1 for the
  blink; `render_recap` header per category; the draw rule for `done` with and
  without movement.
- Screens through the `.playground/fleet-render/` shooters with sources faked,
  read against the mockup.

## Out of scope

- Notifications outside aegis (Telegram, desktop) on `needs_input`.
- Letting the operator set or clear a category by hand beyond opening the tab.
