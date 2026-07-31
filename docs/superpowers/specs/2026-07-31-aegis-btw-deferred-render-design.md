---
title: /btw, deferred — a side note you can watch, read, and call off
date: 2026-07-31
status: design
---

# `/btw`, deferred — a side note you can watch, read, and call off

`/btw` shipped in 9fc7752 and works: you ask a side question, it reads the
pane's own transcript tail, makes one `generate()` call, and mounts an
inline block. The design held up. The *surface* did not.

Three things are wrong with it, and only one of them is cosmetic.

1. The answer renders as flat `Text`. A model asked a technical question
   answers in markdown — lists, code spans, emphasis — and you read the
   asterisks.
2. There is no sign the note is running. You type `/btw …`, press enter,
   and nothing happens for a quarter of a minute.
3. **The pane freezes.** Not "shows no spinner" — freezes.

The third is the real defect, and it is also why the second cannot be
fixed on its own. This spec is mostly about that.

## The freeze

`/btw` is awaited inside the pane's input handler
(`pane.py:1371`, in `on_growing_input_submitted`):

```python
result = await dispatch(payload, CommandContext(bridge=self.app,
                                                handle=self.handle))
```

`on_growing_input_submitted` is a Textual message handler. Awaiting inside
one holds that widget's message pump for the duration of the await, and
9fc7752's own commit message measures the duration at **12-17 seconds**
against a real 32k window. For those 12-17 seconds the `ConversationPane`
processes no messages: the `WorkingIndicator` stops ticking, every running
tool track's spinner (`pane.py:1737`, `_tick_tools`) stops, and the input
stops accepting keys.

So the honest description of `/btw` today is: *a side question that stops
the conversation for fifteen seconds*, which is close to the one thing the
original spec set out to avoid. The tagline — "a side note that doesn't
cost a conversation" — is true about money and false about attention.

You cannot bolt a spinner onto this. A spinner is a timer callback, and
timer callbacks are messages on the pump the await is holding. **The
indicator and the freeze are one fix**: `/btw` has to come off the input
handler, and once it is off, the placeholder block that shows it running
is what naturally fills the gap.

## `deferred`: one flag, two callers

`SlashCommand` (`commands/__init__.py:44`) gains one field:

```python
@dataclass(frozen=True)
class SlashCommand:
    ...
    deferred: bool = False
```

`deferred=True` means: *do not await me in the input handler.* A frontend
that understands the flag mounts a placeholder, runs `dispatch()` off the
handler, and rewrites the placeholder when the result lands. A frontend
that does not understand it — today, the web client — ignores the field
and keeps the current synchronous behaviour. The change is additive and
every existing command keeps the default.

The alternative was `if verb == "btw"` in the pane. It is rejected for a
concrete reason rather than a stylistic one: **`@peer` is the same shape.**
The `@peer` spec (b632d9a, in flight as of this writing) asks an idle peer
agent a question from where you stand — a slow out-of-band call fired from
the same input handler. On the inline-await path it inherits this exact
freeze on day one. Declaring the property on the command means `@peer`
sets `deferred=True` and gets the placeholder, the spinner, and the
cancel rung for free, instead of two commands inventing two mechanisms
that both have to be maintained.

A verb check would have been three lines. It would also have guaranteed
we wrote this spec twice.

## The btw track

`_BtwTrack` mirrors `_ToolTrack` (`pane.py:89`), because the problem is
the same one tool calls already solved: a block that is mounted before its
content exists, ticks while it waits, and freezes when it lands.

```python
@dataclass(slots=True)
class _BtwTrack:
    idx: int              # history index of its block
    start: float          # time.monotonic() at dispatch
    prompt: str           # the question, for the running line
    worker: object        # the Textual worker, for cancel
    done: bool = False
    elapsed: float | None = None
```

It reuses the existing 10 Hz ticker (`_ensure_tool_timer` /
`_tick_tools`, `pane.py:1721-1744`) and `_SPINNER_FRAMES`, and re-renders
by history index through `update_content(..., layout=False)` exactly as
`_render_tool_block` does (`pane.py:1746`). `layout=False` is right for
the same reason it is right there: only the elapsed digits change, on a
line the block already occupies.

Running, it reads:

```
⠹  btw · which of these two paths does the resume flow take?  4.2s
```

The question is echoed because by second twelve you have forgotten which
side question you asked, and because a spinner with no subject is just
anxiety.

### Where the answer lands

The block is rewritten **in place, at the index where it was mounted**.

This matters because `/btw` is legal mid-turn, so the agent may stream
output while the note is in flight. Rewriting in place means the note
appears where you *asked* it, with the agent's output flowing past
underneath — rather than jumping to the tail and interleaving a side
question into the middle of the agent's reasoning. It is the tool-track
behaviour, and it is correct here for a stronger reason than consistency:
the note is a comment on the conversation at the point you made it.

## Markdown

`render_side_note` (`render.py:247`) returns a `Group` instead of a
`Text`: the `btw` header line, `Markdown(note.answer)`, then the
`needs_more` line and the footer unchanged.

The answer loses its explicit `colors.ink` tint. That is the intent, not a
regression — Rich's `Markdown` brings its own styling, which is the whole
reason to use it. The copy payload stays the raw markdown source, since
markdown source is what you want on the clipboard.

`Markdown` parses eagerly in its constructor, which `render.py:151`
already flags as a real cost on long logs. It is not a concern here: it is
constructed once, at completion, for a block that exists a handful of
times per session. The running placeholder is plain `Text` and never
parses anything, so the 10 Hz tick stays cheap.

### Test fallout

Four tests at `tests/test_btw_command.py:129-153` assert on
`render_side_note(...).plain`. A `Group` has no `.plain`, so they move to
rendering through a `Console` and asserting on the captured output.

This is a strict improvement and worth saying out loud: `.plain` asserts
on a string the renderer happened to build, and would survive the
renderable being structurally broken. Rendering through a `Console`
asserts on what reaches the terminal.

## One at a time

**Per pane, not global.** Panes are independent conversations and `/btw`
reads its own pane's transcript; two panes running side notes at once is
two people asking two questions, which is fine.

A second `/btw` in a pane that already has a live track returns a normal
failed `CommandResult`:

> `a side note is already running` — *ESC to cancel it*

Rendered as an ordinary command block. No modal, no beep. The guard lives
in the pane, where the track state is, not in the `_btw` handler in
`commands/builtins/core.py` — the handler is shared with the web client,
which has no track and no way to know.

Everything else stays available while a note runs: normal messages, other
slash commands, the agent's own turn. Only a second `/btw` is refused.
That availability is the entire point of going deferred; without it we
have replaced a freeze with a lock.

## ESC

`action_interrupt` (`app.py:1257`) already runs a precedence ladder, and
the comments there record why each rung exists. A running note gets a rung
**second**:

| # | condition | action |
|---|---|---|
| 1 | a `ModalScreen` is up | dismiss it |
| 2 | **the active pane has a live btw track** | **cancel the note** |
| 3 | the input has half-typed text | clear it |
| 4 | otherwise | interrupt the turn |

The placement is a judgment call, made deliberately and approved: the
spinning block is the live thing on screen demanding attention, and it is
billing by the second. Clearing the input box is always reachable by other
means; interrupting the turn is the expensive, destructive option and
should stay hardest to hit by accident. Cost and salience both point the
same way, so the note goes above the input.

The cost of being wrong is one extra ESC press, and it is visible either
way.

### What cancel does

`worker.cancel()`, then the block is rewritten one last time as a muted
tombstone:

```
btw · cancelled after 4.2s
```

A tombstone rather than removing the block. ESC silently deleting
something you can see reads as a glitch, and the block is the only record
that you spent anything at all.

**No cost is shown.** A cancelled call returns no usage, so we do not know
what it cost, and inventing a number for a line whose entire purpose is
honesty about price would be worse than the omission. Elapsed time is what
we actually know, so elapsed time is what it says.

Cancellation propagates as far as the driver allows. `SideNote` is
best-effort by contract (`btw/__init__.py:106`) — every failure already
comes back as a note with `ok=False` rather than an exception — so a
driver that ignores cancellation and returns late finds its track already
`done` and its result dropped. A side question must never be able to
disturb the conversation it sits beside, and that includes on the way out.

## Scope

**TUI only.** The web client (`web/static/js/app.js:124`,
`applyCommandEffect`) handles `theme` and `clear` and ignores
`side_note` entirely, falling back to the title/body text — so `/btw` on
web is already the plainer surface and is not made worse by any of this.
`AGENTS.md` calls the two UIs co-equal and that remains the goal; the
`deferred` flag is declared on the command rather than in the TUI
precisely so the web client can honour it later without a second design.

Not in scope: changing the window budget, the `text_generation:` warning,
or anything about what `/btw` asks or costs. This is the surface only.

## Testing

The pane logic — deferred routing, the one-at-a-time guard, cancel — is
tested against a fake bridge whose `side_note` blocks on an
`asyncio.Event`. That makes "still running" and "cancelled" deterministic
states rather than timing races, so the tests assert on the track's
actual state instead of sleeping and hoping.

Renderer tests go through a `Console`, per the fallout section above.

The full aegis suite flakes 1-2 inotify/watchdog TUI tests on zion, so the
gate is the btw + commands + render subset run to a clean rc; the full
suite is run as a check and its flakes are read, not treated as red.

One mutation check before this is called done: break the guard so a second
`/btw` is allowed through, and confirm the one-at-a-time test actually
fails. A guard test that cannot fail is worth less than none.
