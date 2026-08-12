# Locking the input while the mic is open

**Status:** implemented 2026-08-12 (`7560b3e`, `0ffb8a0`, `8c3db55`); plan at
`docs/superpowers/plans/2026-08-12-voice-input-lock.md`.
Two deviations during implementation — see *Deviations*, at the end.
**Scope:** the TUI voice path — one new widget, one renamed pane method, the
three `AegisApp` voice transitions, and a correctness fix in
`_apply_voice_text`. No change to `aegis/voice/` (the harp-facing session is
already UI-agnostic and stays that way), and no web-client equivalent: the
web frontend has no voice path today.

## The problem

Two problems, and the smaller-looking one is the bug.

**Typing during a recording is silently discarded.** `action_toggle_voice`
captures the input's contents when recording *starts*
(`tui/app.py:1475`), and when the transcript arrives
`_apply_voice_text` does:

```python
pane.input_widget().value = base + joiner + text
```

That is an assignment, not an append to current state. Anything typed
between pressing the voice key and the transcript landing is overwritten by
the stale `base`. The window spans the whole recording *and* the whole
decode.

**The decode phase is invisible.** `_stop_voice` calls
`pane.set_recording(False)` immediately and then hands off to a background
thread (`tui/app.py:1507-1513`). The border returns to normal, nothing
indicates work is happening, and text appears from nowhere a second or two
later. That silence is precisely the window where a user is most likely to
start typing — into the buffer that is about to be clobbered.

The whole visible state today is a border colour: one CSS class,
`.recording`, at `tui/pane.py:812`.

## The shape of the fix

Lock the input for the full span — record **and** decode — and give that span
a real indicator with a spinner and a timer.

### The lock, and the correctness fix under it

`GrowingInput` is a Textual `TextArea`, which has `read_only` built in
(verified against Textual 8.2.6), so text mutation and paste are handled by
setting it. Submit needs its own guard: `action_submit`
(`tui/widgets.py:100`) is a Textual action rather than a key handler, so it
returns early while locked.

Blocking submit is not cosmetic. Submit clears the input; the pending
`value = base + text` assignment would then *resurrect* the text just sent,
leaving a copy of a sent message sitting in the box.

Separately — and this is the actual defect — **`_apply_voice_text` reads the
input's current value at apply time** instead of the `base` captured at
record-start. `base` is dropped entirely.

Doing both is deliberate. The lock is the user-facing behaviour; the
current-value read is the correctness fix, and it means that if the lock
ever has a hole (a paste path, a future keybinding, another frontend) the
failure mode is "your text and the transcript both survive, in order"
rather than "your text vanishes."

### Two edges that would otherwise strand the input locked

Both are live paths, not hypotheticals:

- **An empty transcript.** `_apply_voice_text` returns early when `text` is
  empty — a recording with no speech in it does exactly this. With a lock
  added, that early return would leave the input read-only and the strip
  spinning forever.
- **A decode failure.** `VoiceSession._finish` (`voice/session.py:118-123`)
  catches every exception and calls `on_final("")`, so the callback always
  fires. That is what makes it a sound unlock hook — but only if the unlock
  runs before the emptiness check.

So: **the unlock is the first thing `_apply_voice_text` does,
unconditionally**, before any check on the text.

### The race the lock would otherwise introduce

`_stop_voice` sets `self._voice = None` *before* calling `voice.stop()`, and
the decode runs off-thread. `action_toggle_voice` gates on exactly that
field (`if self._voice is not None`). So **during decode, a second press of
the voice key starts a fresh recording.** The first decode then lands and
its `on_final` drives the state back to idle — unlocking the input in the
middle of the new recording and leaving the strip describing a state the
session is not in.

This is latent today (two transcripts race into the box, both survive), but
the lock turns it into a stuck or wrongly-cleared state.

**Resolution: a `_voice_decoding` flag, and the voice key is ignored while
transcribing.** Decode is a second or two; queueing a recording behind it is
more machinery than the case earns.

## The indicator

A new one-row widget, `VoiceStrip`, at `src/aegis/tui/voice_strip.py`,
modeled directly on `WorkingIndicator` (`tui/pane.py:243`) — the same
`set_interval(0.1)` tick, the same `_cancel_timers()`-first idempotence so a
restart neither leaks timers nor freezes a glyph, and the same
`set_animating()` seam so a backgrounded pane stops redrawing. Elapsed time
is derived from a `time.monotonic()` start, so a frozen pane's timer is
still correct when it comes back.

It mounts in `main-column` between `PendingStrip` and `GrowingInput`
(`tui/pane.py:1036-1037`), joining the row of single-line strips already
sitting directly above the input. Hidden at 0 height when idle.

```
● Recording  0:04 — ctrl+g to stop
⠋ Transcribing  0:02
```

**Different glyphs for the two states, on purpose.** Recording gets a
pulsing filled dot: a recording light is legible on sight in a way a braille
spinner is not, and it reads as *capturing*. Transcribing gets the braille
spinner `WorkingIndicator` already uses, because it means there what it
means here — the machine is busy and you wait.

Colour follows the existing convention: `$warning` while recording, matching
the `.recording` border rule already at `tui/pane.py:812`, and muted while
transcribing. The input keeps its border tint and gains a transcribing
variant, so the state reads from both the strip and the box.

**The timer resets between the phases.** While recording it answers *"how
long have I been talking?"*; while decoding it answers *"is this stuck?"*.
A single running total answers neither once you are four seconds into a
decode that should take one.

**The strip renders the configured key, not the string "ctrl+g".** The
binding is `self._voice_cfg.key` (`tui/app.py:472`); Ctrl+G is only the
default. Hardcoding it would confidently tell anyone who rebound the key to
press the wrong one — worse than saying nothing.

## State ownership

`ConversationPane.set_recording(bool)` (`tui/pane.py:1792`) becomes
`set_voice_state(state)` over `"idle" | "recording" | "transcribing"`. One
method sets the CSS class, drives the strip, and toggles `read_only`, so the
three cannot drift apart. `AegisApp` owns the transitions, as it already
owns `_voice` and `_voice_pane`.

The three transitions:

| when | state |
|---|---|
| `voice.start()` returns without raising | `recording` |
| `_stop_voice`, before `voice.stop()` | `transcribing` |
| `_apply_voice_text`, first statement | `idle` |

A `start()` that raises is already handled — it notifies and returns
(`tui/app.py:1486-1489`) — and never enters `recording`, so no unlock is
owed.

## Tests

`app._voice_session_factory` is already an injection seam;
`tests/test_voice_action.py` swaps a `_StubVoice` into it. Everything below
runs under the Textual pilot with no mic and no model.

- the input is `read_only` while recording **and** while transcribing, and
  editable again afterwards
- submit is refused while locked
- **text typed before recording survives, and the transcript appends after
  it** — the data-loss regression, asserted on the input's resulting value
- **an empty transcript still unlocks** — the strand edge
- **a decode failure still unlocks** — `_finish` always calls `on_final`
- **a second voice-key press during transcribing is ignored** — the race
- the strip renders the configured key, proven by setting a non-default one
  rather than by reading the default back
- strip states, elapsed formatting, and idempotent restart leaking no timers

## Out of scope

- **A discard path.** No way to abandon a recording without transcribing it.
  Escape already carries four meanings (priority app interrupt, modal
  dismiss, `/btw` cancel, completion-palette hide), and a transcript lands as
  editable text rather than a sent message, so the undo is select-all-delete.
  Revisit if abandoned recordings turn out to be common.
- **Streaming or partial transcripts.** The session decodes the whole clip on
  stop (`voice/session.py:66-78`); this changes none of that.
- **A web-client equivalent.** `VoiceStrip` is a Textual widget and the web
  frontend has no voice path to lock.

## Deviations

**The palette has no `warning`.** This spec said the strip would be
`$warning` while recording, which is true of the *CSS* — `$warning` is a
Textual design token and the `.recording` border rule uses it. But the
strip's Rich text takes a colour from `AegisColors`, and that dataclass has
no such field. The right one is `working`, which `aegis_colors()` maps from
`theme.warning` — the same colour, reached through the palette instead of
the stylesheet. Caught by the plan's self-review, before any code.

**One planned test asserted nothing.** The spec asked for "text typed before
recording survives, and the transcript appends after it". Written that way,
the test passes against the *unfixed* code: the old `base` capture handles
text typed before the recording correctly. The defect is text changing
*between* the capture and the delivery — and the lock now makes that
unreachable through the UI, so pinning it requires writing the buffer
directly and asserting at the seam. `test_transcript_appends_to_the_value_
at_delivery_time` does that, and fails against the old code with
`'at start spoken words'`. The weaker test was kept as well, since it guards
the ordinary append path.

The lesson generalises: a test whose name describes the *feature* rather
than the *failure* can pass before the fix exists. Both were run against
unfixed code, which is the only reason the difference showed up.
