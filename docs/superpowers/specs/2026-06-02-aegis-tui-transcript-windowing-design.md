# Aegis TUI Transcript Windowing — Design

**Status:** approved
**Date:** 2026-06-02
**Scope:** `src/aegis/tui/pane.py` (ConversationPane only)

## Problem

Long sessions render thousands of `CopyableBlock` widgets into a single
`VerticalScroll`. Each block is a full Textual `Widget` (hover state, click
handler, tooltip, `Static` child). Textual's layout pass scales poorly past
a few thousand widget nodes, so scroll latency degrades visibly during
multi-hour sessions.

## Goal

Cap the mounted widget count at a small constant (~300) without losing
scrollback. Older blocks are still navigable by scrolling up; they re-mount
in batches on demand.

## Architecture

### Data model on `ConversationPane`

```python
@dataclass(slots=True)
class BlockRecord:
    renderable: RenderableType
    payload: str
    tight: bool
    kind: str | None = None   # "text" | "thinking" | None — used by streaming

self._history: list[BlockRecord] = []
self._window_start: int = 0              # first mounted index
self._stick_to_bottom: bool = True
self._loading_older: bool = False
self._streaming_history_idx: int | None = None
```

The mounted window is implicitly `_history[_window_start : len(_history)]` —
the tail is always mounted. `_window_start` only moves forward (eviction) or
backward (load-older).

### `_mount_block` chokepoint

Every block creation funnels through `_mount_block`:

1. Append a `BlockRecord` to `_history`.
2. Create + mount the `CopyableBlock` widget (positioned before the
   `WorkingIndicator` if present, else at end).
3. If `self._stick_to_bottom`: `transcript.scroll_end(animate=False)`.
4. If `self._stick_to_bottom` and `len(_history) - _window_start > N_MAX`:
   evict the top `EVICT_BATCH` widgets, advance `_window_start`.

Eviction never runs while the user is scrolled up — they keep reading
without their content disappearing.

### Sticky-bottom tracking

In `on_mount`, after the transcript exists:

```python
self.watch(transcript, "scroll_y", self._on_scroll)
```

`_on_scroll` recomputes two flags whenever `scroll_y` changes:

- `_stick_to_bottom = (max_scroll_y - scroll_y) <= STICKY_EPS`  (~2 rows)
- `near_top = scroll_y <= LOAD_MORE_EPS` (~3 rows)

If `near_top` and `_window_start > 0` and not `_loading_older`, schedule
`set_timer(DEBOUNCE_S, self._load_older)`. The timer is single-shot and
re-armed on each scroll movement that lands near the top — the debounce
absorbs wheel bursts.

### Load-older (scroll-up)

```python
def _load_older(self) -> None:
    if self._loading_older or self._window_start == 0:
        return
    self._loading_older = True
    try:
        new_start = max(0, self._window_start - LOAD_BATCH)
        # Snapshot anchor before mutation
        first = self._first_mounted_block()
        anchor_y = (first.region.y - transcript.region.y) if first else 0
        # Mount older records at the top, in order
        for rec in self._history[new_start : self._window_start]:
            block = CopyableBlock(rec.renderable, rec.payload, tight=rec.tight)
            transcript.mount(block, before=first)
        self._window_start = new_start
        # Restore anchor on next layout tick
        def _restore() -> None:
            new_first_y = first.region.y - transcript.region.y if first else 0
            transcript.scroll_to(y=transcript.scroll_y + (new_first_y - anchor_y),
                                 animate=False)
        self.call_after_refresh(_restore)
    finally:
        self._loading_older = False
```

The anchor preservation is the only subtle bit — without it the user gets
yanked to the very top whenever a batch loads.

### Initial replay

`_mount_replay` populates the entire `_history` (so scrollback covers the
full prior session) but only mounts the last `N_MAX` widgets:

```python
def _mount_replay(self) -> None:
    if self._replay is None:
        return
    # Drive every event through _on_core_event so _history fills.
    # _on_core_event already calls _mount_block which appends to _history;
    # we need to suppress widget mounting for everything before the tail.
    # Simplest: drive all events, then evict to N_MAX.
    for ev in self._replay.events:
        self._on_core_event(None, ev)
    if self._replay.interrupted:
        self._flush_streaming()
        self._mount_block(Text("⚠ interrupted", style="yellow"),
                          "⚠ interrupted")
    # Trim down to last N_MAX
    self._trim_to_window()
```

`_trim_to_window` unmounts widgets above index `len(_history) - N_MAX` and
sets `_window_start` accordingly. Replay therefore costs ~one mount per
event during boot (same as today) and then immediately drops down to the
windowed count. A future optimisation could skip the intermediate mount —
out of scope for v1.

### Streaming aggregation

`_stream_append` already mutates the live `CopyableBlock` via
`update_content`. With windowing it must also keep the corresponding
`BlockRecord` in sync, otherwise scrolling back through a long streamed
turn would show stale chunks.

When the first chunk of a stream is mounted, capture the index:

```python
self._streaming_history_idx = len(self._history) - 1
```

On subsequent chunks, mutate the record in place:

```python
rec = self._history[self._streaming_history_idx]
rec.renderable = r
rec.payload = self._streaming_text
```

`_flush_streaming` clears `_streaming_history_idx`.

### Constants (module-level in `pane.py`)

```python
N_MAX = 300           # max mounted blocks at any time
EVICT_BATCH = 50      # widgets dropped per eviction
LOAD_BATCH = 100      # widgets restored per scroll-up trigger
STICKY_EPS = 2        # rows from bottom counted as "at bottom"
LOAD_MORE_EPS = 3     # rows from top counted as "near top"
DEBOUNCE_S = 0.15
```

Not exposed in `.aegis.yaml` for v1. Easy to add later as a `[tui]` block
if profiling on real sessions shows the defaults are wrong.

## What this design does NOT include (deferred)

- "↓ N new messages" pill while scrolled up.
- Per-project tuning of `N_MAX` / batch sizes via YAML.
- Persistence of scroll position across session restarts.
- A "jump to top / bottom" keystroke.

## Testing strategy

Hermetic Textual tests using `AegisApp(...).run_test()`, driving events
directly via `pane._on_core_event(None, ev)`. New file:
`tests/test_pane_windowing.py`. Each test asserts on:

- `len(pane._history)` — total recorded events.
- `pane._window_start` — first mounted index.
- `len(pane.query(CopyableBlock))` — actual widget count.
- `pane._stick_to_bottom` — sticky flag.

Existing `tests/test_pane_replay.py` covers the pure `replay_blocks` helper
and is unaffected. Existing `tests/test_tui.py::test_submit_sends_renders_and_bells`
must continue to pass — it relies on `_transcript_has` walking
mounted blocks, and the values asserted there fit well inside `N_MAX`.

## Files touched

- `src/aegis/tui/pane.py` — all changes.
- `tests/test_pane_windowing.py` — new.
