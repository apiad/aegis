# Transcript on the Line API

*Status: SUPERSEDED, not implemented. Do not build this.*

*The case for it rested on round-2 measurements that were a benchmark
artefact: `await pilot.pause()` inside the timed region, whose own cost
is O(mounted widgets) (`textual/pilot.py:490`). A real layout pass costs
~6.3 ms at the shipped window, not ~80 ms, and a mounted block costs
~0.033 ms, not 0.21 ms. This rewrite would remove ~5 ms from a 6.3 ms
frame in exchange for ~1750 lines and ~42 ported tests.*

*The real cost was 140 layout passes per turn-second from two 10 Hz
timers, fixed with two keyword arguments. See round 3 of
`2026-07-29-tui-performance-audit.md`.*

*Kept because its findings are worth having if this is ever revisited:
the `virtual_size` layout trap, the strip-render costs, and the audit of
what a childless transcript would have to reproduce. An adversarial
review also found the design silently dropped ten affordances (the
working indicator's position inside the transcript, the resume banners,
`_transcript_has`'s query — which would have failed silently — `/clear`,
result-age repaint, inter-record margins, palette changes flushing the
strip cache, and width-baked renderables), mis-stated the hit test
(mouse offsets are region-relative; subtract `gutter.top_left`), assumed
a tooltip mechanism that does not work with one widget
(`screen.py:1660`), and would have had to hand-render the selection
highlight that Textual currently paints for free.*

## The problem

Textual rebuilds the whole compositor map on any layout pass, so the cost
of a layout is linear in mounted widget count. The transcript mounts one
widget per entry. Round 2 removed the *needless* layout passes and halved
the mounted window; what remains is that the necessary ones still scale
with how much transcript is mounted.

Measured after round 2, at the `N_MAX = 150` window:

| | cost |
|---|---|
| one full reflow | ~80 ms |
| marginal cost of one mounted block | ~0.21 ms |
| `_load_older` batch (40 blocks) | ~273 ms |
| eviction hitch (`EVICT_BATCH = 50`) | ~151 ms |

So ~32 ms of every 80 ms reflow is the transcript, and the floor — app
chrome plus Textual itself — is ~48 ms. Worse, **audit finding 5 is still
open**: while scrolled up the mounted window grows without bound (652
blocks and climbing, measured), so the tax is unbounded on exactly the
path a user takes to reread a long thread.

## What this buys, honestly

On the happy path (sticky at the bottom) this is worth **~1.5x**, not
another 3x: reflow ~80 → ~48 ms, keystroke ~105 → ~73 ms. If that were
the whole case it would not justify a pane rewrite.

The case is the other three:

- `_load_older`'s 273 ms hitch disappears — nothing is mounted, so
  scrolling back is rendering, not widget construction at ~6.8 ms apiece.
- The 151 ms eviction hitch disappears, along with the entire windowing
  subsystem.
- Finding 5 stops being expressible. Cost becomes flat at any thread
  length and any scroll position, permanently.

It converts "fast until the thread gets long or you scroll up" into
"flat, always", and deletes a subsystem.

## Approach

One childless `TranscriptView(ScrollView)` rendering visible lines on
demand — Textual's documented answer for long scrollable content ("a
ScrollView should be a line-api widget, so it won't have children").
`BlockRecord` and `pane._history` are unchanged; they are already the
right data model, and `materialize()` is already lazy. This is a pane
rewrite, not an engine rewrite.

### Rejected: precompute every record's height

Exact scrollbar from frame one and much simpler code. Rejected on
measurement: rendering a record to `Strip`s costs ~1.4 ms weighted mean
at width 110 (tool call 0.23 ms, tool result 0.66 ms, Markdown prose
3.9 ms, long message 6.2 ms), and a real session log holds ~18,000
events — **24.7 s** at resume and on every terminal resize.

This is the version most people reach for once the transcript is "just
one widget". The number above is why it does not work.

### Rejected: hybrid — Line API history, real widget for the live block

Keeps streaming a `Static.update()` nobody has to think about. But it
puts a mounted widget back in the tree, so the live block still triggers
a layout on every repaint, and it adds scroll-math and ordering
complexity where the two models meet. The 20/s repaint throttle already
made streaming repaints cheap, so this pays complexity for nothing.

## Architecture

New module `src/aegis/tui/transcript.py`. `ConversationPane` composes
`TranscriptView` in place of `VerticalScroll`; input, status bar and
pending strip are untouched.

### The height index

Mapping a scroll position to a record needs every record's height, which
is exactly what cannot be computed eagerly. So heights are known for a
**contiguous window that grows on demand**:

- `_heights` per record in the known window, `_offsets` as prefix sums
  for `bisect`.
- Append: compute one height (~1.4 ms), extend offsets. O(1).
- Scrolling toward the top extends the window upward in batches. This is
  the existing `_load_older` concept moved down one layer: a batch of 40
  costs ~55 ms of rendering rather than 273 ms of widget construction.
- `virtual_size` covers the known window, so **the scrollbar describes
  history indexed so far** and can settle under the user when they fling
  toward the top of a long thread. This is the design's one accepted
  user-visible regression; it replaces a 273 ms freeze with a brief
  inaccuracy.

`Strip`s live in a bounded LRU cache (a few hundred records) — 18,000
records × ~20 lines is not something to keep resident. Heights are ints
and stay for the whole known window.

### The quiet-update rule

`Widget.virtual_size` is `Reactive(Size(0, 0), layout=True)`
(`textual/widget.py:357`), so the obvious `self.virtual_size = Size(...)`
fires a full layout — the exact cost this rewrite exists to remove.
`RichLog` does precisely that on every write, so it is *not* a safe
reference for the mutation path.

Textual's own escape hatch, from `Widget._size_updated`
(`textual/widget.py:4137`):

```python
self.set_reactive(Widget.virtual_size, size)
self._scroll_update(size)
```

Measured in a Textual app with 150 sibling widgets:

| operation (×10) | layout passes |
|---|---|
| `self.virtual_size = Size(...)` | 10 |
| `set_reactive` + `_scroll_update` | 0 |
| `refresh_lines(start, count)` | 0 |

Every mutation path uses the quiet form and repaints via `refresh_lines`,
never `refresh()`. **This invariant gets its own test**: without one,
someone writes the obvious assignment in a year and silently restores the
tax with no visible symptom.

### Interaction

One handler set on the view, dispatching by index:
`idx = bisect_right(_offsets, event.y + scroll_y) - 1`.

- **hover** — `on_mouse_move` sets `_hover_index`; `refresh_lines` the
  old and new ranges only. The tint is applied at `render_line` time via
  `Strip.apply_style`, so cached strips stay un-tinted and hover costs no
  re-render.
- **tooltip** — set `self.tooltip` when `_hover_index` changes; the same
  three variants as today (copy / expand args / openable tokens).
- **click, ctrl+click, alt+click** — resolve to a record, then call the
  *existing* copy / `_open_file_from_tokens` / `_open_natively_from_tokens`
  logic. Moved, not rewritten.
- **selection** — implement `get_selection` over record payloads.
  Textual's default returns `None` unless `_render()` yields
  `Text`/`Content` (`textual/widget.py:4227`), and transcript blocks
  render Markdown and `Group`, so selection is already broken today.
  One widget spanning the transcript makes this a small gain.

### Subagent boxes become a record kind

`SubagentBox` already builds its body as a `rich.Group` of child
`BlockRecord`s (`pane.py:_body_renderable`). So it collapses to a
`BlockRecord` variant carrying `children`, `footer` and `collapsed`;
`materialize()` returns the Group; toggling flips the flag and
invalidates one record. No nested line ranges, no nested widgets.

### Streaming

Append a record, then on each delta update the record and invalidate it.
It is the last record, so offsets update in O(1). The existing
`STREAM_REPAINT_S` (20/s) throttle carries over unchanged.

### Resize

Invalidate the known window only — bounded by how far the user has
scrolled, typically hundreds rather than thousands. Recompute the visible
rows first so the screen is immediately correct, then walk outward in a
worker, adjusting `virtual_size` (quietly) as it goes.

## What gets deleted

`N_MAX`, `EVICT_BATCH`, `LOAD_BATCH`, `_window_start`,
`_mounted_blocks`, `_evict_top`, the widget half of `_load_older`,
`CopyableBlock`, `SubagentBox`-as-widget, and audit finding 5.

`transcript_constants.py` keeps `STREAM_REPAINT_S`, `STICKY_EPS`,
`LOAD_MORE_EPS`, `DEBOUNCE_S` and the tool-result head limits. The web
client reads `N_MAX`/`LOAD_BATCH` from that module
(`web/server.py:31`), so its `hello` constants block must be updated in
the same change.

## Testing

Structural, not wall-clock — a timing assertion flakes on a loaded box,
as the round-1 audit's own discarded numbers showed.

- No layout pass escapes append / stream / toggle / hover (spy on
  `Widget.refresh`, assert zero `layout=True`). This is the headline
  invariant.
- `y → record` mapping correct across window extension, invalidation and
  resize.
- Height index stays consistent with rendered strips — property test over
  randomly-sized records. An invalidation that updates strips but not
  heights misaligns every hit-test below it, and presents as "clicks land
  on the wrong block" rather than as an index bug.
- Every gesture fires on the right record.
- Existing pane tests port over wherever they assert behaviour rather
  than widget structure.

## Risks

- **Offset drift** — the failure above. Mitigated by the consistency
  property test.
- **Scrollbar settling** — accepted, described above.
- **Interaction surface is larger than it looks.** The design names eight
  affordances from a ~1750-line pane; anything missed becomes a
  regression a user hits before a test does.

## Out of scope

The web client's own JS renderer (`coalesce.js` / `renderEvent.js`). It
mirrors the transcript but has a separate implementation and a different
performance profile; only its constants block is touched here.
