---
date: 2026-06-02
type: design
status: draft
area: aegis/tui
---

# Transcript windowing for long sessions

## Problem

After a long session (hundreds to thousands of events), the TUI becomes
sluggish — scrolling, focus changes, and even unrelated tab switches stall.
The cause is the transcript widget tree: every event in
`ConversationPane._on_core_event` becomes a `CopyableBlock` widget (a
`Widget` with hover/click handlers, tooltip, and a `Static` child), all
mounted into one `VerticalScroll`. Past ~2k events the tree is 6–8k
Textual nodes and the framework's layout/reflow cost dominates every
interaction.

The events themselves are not the problem — JSONL persistence and Rich
rendering are cheap. The cost is **mounted Textual widgets**.

## Goal

Cap the number of mounted blocks at a fixed `N_MAX`. Older blocks remain
addressable: when the user scrolls to the top of the visible window, the
next batch loads in, anchored to the user's current viewport so there's no
visual jump. Newer blocks remain interactive (click-to-copy, ctrl-click-
to-open-file) exactly as they are today — the design preserves UX, it
only changes the mounted-widget count.

Live events also gain a **sticky-bottom** behavior: auto-scroll to the
tail when the user is already at the bottom, but do nothing if the user
has scrolled back to read history.

## Non-goals

- No "↓ N new messages" pill when the user is scrolled up while live
  events arrive. Easy to add later; out of scope here.
- No per-project tuning via `.aegis.yaml`. Constants live in `pane.py`
  for v1 and can be promoted to config if a future use-case demands it.
- No change to event persistence, the JSONL log, the replay path, or
  `render_event`. The windowing is purely a `ConversationPane` concern.

## Design

### Data model

`ConversationPane` gains two pieces of state:

```python
@dataclass(slots=True)
class BlockRecord:
    renderable: RenderableType
    payload: str
    tight: bool

self._history: list[BlockRecord] = []
self._window_start: int = 0       # first index of _history that is mounted
# _window_end is implicit: == len(self._history). The tail is always mounted.
self._stick_to_bottom: bool = True
self._loading_older: bool = False
```

`_history` is the full ordered list of blocks ever rendered for this
pane. `_window_start` is the index of the first one currently in the
widget tree. The mounted set is therefore a contiguous suffix
`_history[_window_start:]`, which is at most `N_MAX` long under normal
flow.

### Mounting a new block (live event)

`_mount_block` is the single chokepoint. It now:

1. Builds a `BlockRecord` from `(renderable, payload, tight)` and
   appends it to `_history`.
2. Mounts a `CopyableBlock` widget, preserving the existing
   `before=working_indicator` insertion semantics.
3. If `self._stick_to_bottom` is `True`, calls
   `transcript.scroll_end(animate=False)` (preserves the current UX).
4. If `len(self._history) - self._window_start > N_MAX` **and**
   `self._stick_to_bottom` is `True`, evicts the top `EVICT_BATCH`
   widgets (unmounts them) and advances `self._window_start`.

The eviction predicate ties to sticky-bottom: a user mid-scrollback is
*never* yanked. They can let the mounted set grow temporarily while they
read; the moment they scroll back to the bottom and the next live event
arrives, eviction catches them back up to `N_MAX`.

The streaming-block aggregator (`_stream_append`, `_streaming_block`,
`_streaming_text`) keeps its current shape. Its in-place
`update_content` calls do not move the scroll position; on the first
chunk that mounts the streaming block, `_mount_block` runs the sticky
logic above. Subsequent token chunks update the same widget in place.
The streaming block must also keep `_history[-1]` in sync — when a
token is appended, the corresponding `BlockRecord.renderable` and
`.payload` are mutated in place so a future remount of that block
(after eviction + scroll-up) produces the final, complete content. The
existing `_flush_streaming` simply clears the streaming pointers; no
change to history.

### Sticky-bottom flag

A reactive watch on `transcript.scroll_y` updates the flag whenever the
user scrolls:

```python
def watch_scroll_y(self, _old, _new) -> None:
    t = self._transcript()
    self._stick_to_bottom = (t.max_scroll_y - t.scroll_y) <= STICKY_EPS
    self._maybe_schedule_load_older()
```

Textual's `VerticalScroll` already exposes `scroll_y` as a reactive
attribute. We subclass `VerticalScroll` minimally (or use a watcher on
the existing instance via `self.watch(transcript, "scroll_y", ...)`)
to receive the callback on the pane.

`STICKY_EPS = 2` rows of tolerance: a user one row off the bottom is
still considered "at the bottom" so wheel-jitter doesn't flip the
flag spuriously.

### Loading older blocks on scroll-up

The same watcher checks the near-top predicate:

```python
def _maybe_schedule_load_older(self) -> None:
    if self._loading_older or self._window_start == 0:
        return
    t = self._transcript()
    if t.scroll_y > LOAD_MORE_EPS:
        return
    self._loading_older = True
    self.set_timer(DEBOUNCE_S, self._load_older)
```

Debounce (`DEBOUNCE_S = 0.15`) coalesces wheel-scroll bursts: a user
holding scroll-up triggers one batch load, not ten.

`_load_older` mounts `LOAD_BATCH` widgets at the top of the transcript,
preserving the user's viewport:

```python
def _load_older(self) -> None:
    try:
        new_start = max(0, self._window_start - LOAD_BATCH)
        if new_start == self._window_start:
            return
        t = self._transcript()
        # Anchor: pick the first currently-mounted block; remember its
        # on-screen Y relative to the transcript viewport, and the
        # transcript's current scroll_y.
        first = next(iter(t.query(CopyableBlock)), None)
        anchor_y_before = (
            (first.region.y - t.region.y) if first is not None else 0
        )
        scroll_before = t.scroll_y

        # Build + mount widgets for _history[new_start : _window_start],
        # in order, each before the previous first-mounted block.
        before = first
        widgets = []
        for rec in self._history[new_start:self._window_start]:
            w = CopyableBlock(rec.renderable, rec.payload, tight=rec.tight)
            widgets.append(w)
        # Textual supports mount(*widgets, before=node) for batched insert.
        t.mount(*widgets, before=before)
        self._window_start = new_start

        # After layout settles, restore the anchor. The first block we
        # remembered is now further down — shift scroll_y by the height
        # of newly inserted content so it lands at the same on-screen Y.
        def _restore() -> None:
            if first is None:
                t.scroll_to(y=scroll_before, animate=False)
                return
            anchor_y_after = first.region.y - t.region.y
            delta = anchor_y_after - anchor_y_before
            t.scroll_to(y=t.scroll_y + delta, animate=False)
        self.call_after_refresh(_restore)
    finally:
        self._loading_older = False
```

The anchor is the simplest correct strategy: pick a block that exists
both before and after the mount, measure where it was, measure where it
ended up, shift scroll by the delta. Textual handles measurement after
layout via `call_after_refresh`.

### Initial replay

`_mount_replay` is rewritten to populate `_history` for *every* replayed
event, but only mount the last `N_MAX`. The current
`for ev in self._replay.events: self._on_core_event(None, ev)` loop is
preserved logically but split: each event becomes a `BlockRecord`
appended to `_history`; only those with index `>= len(_history) - N_MAX`
are mounted. The streaming aggregator must not run during replay (events
are flushed; replay has no live token streams to coalesce — coalescing
already happens upstream in `coalesce_chunks`).

The interrupted marker (`⚠ interrupted`) is the final block of the
replay if present, so it lands in the mounted window naturally.

Resumed sessions therefore boot fast even after a 5000-event session:
mount cost is bounded by `N_MAX`, scroll-up reveals the rest.

### Constants

Module-level in `src/aegis/tui/pane.py`:

```python
N_MAX = 300           # mounted blocks at steady state
EVICT_BATCH = 50      # how many to drop from the top per eviction
LOAD_BATCH = 100      # how many to load on scroll-up
STICKY_EPS = 2        # rows of slack for "at the bottom"
LOAD_MORE_EPS = 3     # rows from the top that trigger load
DEBOUNCE_S = 0.15     # scroll-up debounce
```

`N_MAX = 300` is a starting guess based on the symptom (~2k blocks =
sluggish) with ~6× headroom. We can revisit after measuring.
`EVICT_BATCH < N_MAX - LOAD_BATCH` so that immediately after an
eviction the user can still scroll up and reveal at least one batch
before another fetch fires.

## Interactions with existing features

- **WorkingIndicator**: unchanged. It's mounted at the end of the
  transcript and new blocks insert `before=ind`. Eviction unmounts from
  the top — the indicator is never touched.
- **QueueStrip**: lives outside the transcript scroll container. No
  interaction.
- **Resume banner / failure banner**: mounted with
  `mount(banner, before=transcript.children[0])`. With windowing, this
  banner is mounted before the first *currently visible* block, not
  before historical block 0. If the user has scrolled up and loaded
  older blocks, the banner stays at the top of the scroll container
  (because we always mount it before whatever is currently first); on
  load-older, new blocks insert below the banner. That's the desired
  behavior — the banner is a pane-level annotation, not a block.
  Implementation detail: pass the banner widget as a permanent "first
  child" pointer in `_load_older` so widgets mount after it rather than
  before it.
- **Per-pane JSONL log**: unchanged. `make_session_log_observer` still
  fires per event; windowing is purely a presentation-layer concern.
- **Click-to-copy / ctrl-click-to-open-file**: unchanged. Remounted
  blocks are full `CopyableBlock` instances with identical
  payload + token state.

## Testing

Hermetic tests, no live driver needed. Drive `ConversationPane` directly
in a Textual `App` test harness with a stub `HarnessSession`.

1. **Cap at N_MAX**: feed `_on_core_event` with `N_MAX + 100`
   non-streaming events while sticky-bottom is true (default). Assert
   `len(query(CopyableBlock)) <= N_MAX` after every batch.
2. **No eviction while scrolled up**: scroll the transcript to the top,
   feed `N_MAX + 100` events. Assert mounted count grows (no eviction)
   and scroll position stays at the top.
3. **Load older on scroll-up**: feed 500 events, scroll to top.
   Trigger the scroll watcher; await debounce. Assert `_window_start`
   decreased by `LOAD_BATCH` and the prepended blocks are
   `_history[new_start:old_start]` in order.
4. **Anchor preservation**: after load-older, the previously-first block
   should be visible at approximately the same on-screen position
   (within 1 row of slack). Verify via `block.region.y` before/after.
5. **Replay populates history but mounts only N_MAX**: build an
   `EventReplay` with 500 events; mount pane. Assert
   `len(self._history) == 500`, `len(query(CopyableBlock)) <= N_MAX`,
   `_window_start == 500 - N_MAX`.
6. **Streaming block survives eviction + scroll-up**: stream a long
   AssistantText that completes; then push enough other events to
   evict the streamed block; scroll up; assert the remounted block's
   rendered text matches the fully-streamed content.
7. **Sticky flag toggles on scroll**: programmatically set
   `transcript.scroll_y` to various values; assert
   `_stick_to_bottom` matches `(max_scroll_y - scroll_y) <= STICKY_EPS`.

## Implementation order

1. Introduce `BlockRecord` + `_history` + `_window_start`. Wire
   `_mount_block` to append. No eviction yet, no windowing — just
   bookkeeping. All existing tests still pass.
2. Add the sticky-bottom flag and watcher; gate `scroll_end` on it.
3. Add eviction in `_mount_block` (sticky-only).
4. Add `_load_older` + debounced scroll-up trigger.
5. Rewrite `_mount_replay` for windowed startup.
6. Tests.

Each step is independently committable. Steps 1–3 already deliver the
perf win for steady-state long sessions; step 4 closes the UX hole
(scroll-up to see old history).

## Open questions

None — knobs are guessed reasonable, expose-to-config is deferred,
"↓ N new" pill is deferred.
