# F3 side dashboard — design

**Status:** implemented 2026-08-07 (`5d44bc8`, live-pass fixes in `e36b636`;
the mode went app-wide rather than per-pane on 2026-08-07 — see below)
**Scope:** TUI only. The web client is explicitly out of scope.

## The problem

`F3` today opens `PlanDock` — a second view of the plan, beside a
`PlanStrip` that is already showing the same plan one row lower. It is a
drill-down on one subject, not a mode.

Meanwhile the bottom of every pane has accumulated four rows of ambient
state that are all competing for one horizontal line each:

| row | source | shape |
|---|---|---|
| `QueueStrip` | `QueueDigest` | one line, reformats itself at 1 / 2–3 / 4+ queues |
| `MonitorStrip` | `MonitorManager` | one row *per monitor*, stacked |
| `PlanStrip` | the pane's `PlanTracker` | one line, truncated to width |
| `StatusBar` | pane + app pushes | eight segments through `fit()`'s priority ladder |

Every one of them is solving the same problem — *too much state, one row* —
and each solves it separately by throwing information away. `fit()` exists
precisely because Textual clips an over-long status line silently.

A terminal is usually much taller than it is full. The fix is to spend the
vertical axis: fold all four rows into the column `F3` already opens, and
let the main column be the conversation.

## The shape

`F3` toggles a **mode**, not a widget — and the mode belongs to the app,
not to a pane. Shipped per-pane first, and the cost showed up immediately:
switching tabs changed the layout under you, and every new tab landed
collapsed beside its open siblings. A mode is how you want to *read*
aegis; scoping it to one tab made it read as a widget again. So `F3`,
`/tasks` and `toggle_task_dock` all flip one app-level flag
(`AegisApp.sidebar_mode`), which fans out to every pane, and a pane
adopts it at mount.

**Open** — the sidebar holds every ambient surface; the main column is
transcript and input:

```
┌────────────────────────┬──────────────────────────────┐
│ transcript             │ SESSION                      │
│                        │ fix the eviction race        │
│                        │ opus · high · local          │
│                        │ ● working   2m14s            │
│                        │ ⟳ loop 3/20                  │
│                        │                              │
│                        │ CONTEXT                      │
│                        │ 142k/200k · $1.84 · 12 turns │
│                        │ quota 47% · resets in 3h     │
│                        │                              │
│                        │ PLAN                     3/7 │
├────────────────────────┤ ● parse the header      0:42 │
│ [ type a message… ]    │ ◐ writing the parser    1:20 │
│                        │ ○ wire the strip             │
│                        │                              │
│                        │ QUEUES                       │
│                        │ build   ●1/2 ○3 ✓5           │
│                        │ review  ●0/1                 │
│                        │                              │
│                        │ MONITORS                     │
│                        │ pytest                       │
│                        │ ▓▓▓▓▓░░░ 62% · ETA 1:40      │
│                        │                              │
│                        │ SYSTEM                       │
│                        │ cpu 34% ram 61% disk 82%     │
│                        │ aegis 0.32.0                 │
└────────────────────────┴──────────────────────────────┘
```

**Closed** — exactly today's pane, unchanged. The strips and the status bar
come back; nothing about the collapsed mode is being redesigned.

The sidebar runs the **full pane height**, past the input, rather than
stopping at the transcript the way `PlanDock` does. Six sections want every
row they can get, and a sidebar that stops two rows short of the bottom
reads as a panel that failed to reach rather than a deliberate edge.

### Section order

Ordered by volatility, highest first — what changes and what demands action
at the top, what never changes at the bottom. This is the same principle as
`StatusBar`'s priority ladder (`P_CONNECTION` 70 … `P_SYSTEM` 10), turned
ninety degrees: on a short terminal the sidebar scrolls, and what you see
without scrolling should be what moves.

An empty section renders **nothing at all** — no heading, no blank line.
That is already how the strips behave (`PlanStrip.display = bool(state)`,
`QueueStrip.-empty`), and it is what keeps the panel honest on a session
with no plan and no queues.

`CONNECTION` has no section of its own: `⚠ disconnected — reconnecting…`
is prepended to `SESSION`, because a disconnected session is a fact about
the session and burying it under its own heading at some scroll offset is
the one placement that would make it worse than the status bar.

## Components

### `fit.py` — add `fit_rows(segments, width) -> list[str]`

A sibling of `fit()`, reusing the same `Segment` dataclass (`key`, `tiers`,
`priority`).

Where `fit()` composes segments into **one line** and degrades by priority
until the line fits, `fit_rows` gives each segment **its own row** and picks,
per segment, the widest tier that fits the column. A segment whose narrowest
tier still overflows is dropped rather than truncated — a half-word of a
number is worse than no number.

`priority` is unused by `fit_rows` and that is deliberate: in a vertical
layout segments do not compete for the same space, so there is nothing to
rank. The field stays because the same `Segment` values are consumed by both
functions.

Pure, no Textual import, tested as a plain function — the property `fit.py`
already has and the reason the degradation logic is trustworthy today.

### `tui/sidebar.py` — new

The shape this codebase already uses for every strip (`strip.py`,
`monitor_strip.py`, `plan/render.py`): a pure renderer plus a thin widget.

- `render_sidebar(model, palette, width) -> Text` — pure. Composes the
  section renderers, each a pure function returning `Text | None`, where
  `None` means the section does not appear.
- `Sidebar(VerticalScroll)` — the widget. Scrolls, because a fully populated
  panel is ~25 rows and an 80×24 terminal has about twenty to give.

### `SidebarModel`

A plain dataclass: the snapshot being rendered — title, identity, state,
loop, metrics, quota, plan state + subplans, queue snapshot, monitor views,
system stats, connection. Assembled from exactly the sources the strips and
the bar already read. **No new data path**, no new observer, no new
subscription.

### `PlanDock` is absorbed, not kept

The sidebar's `PLAN` section calls `render_plan_dock` **verbatim**.

That function already solves the two width traps a fresh implementation
would re-pay in full:

- **circles are always space-separated** — they are East Asian Ambiguous,
  Rich measures one cell, terminals draw two, and neighbours overlap;
- **a dock row is `glyph + space + label + space + a 6-cell clock`, so the
  label budget is `width - 9`** — and `size` is already the content box, so
  the widget's padding must not be subtracted from it a second time. Both
  errors were live at once and cancelled at some widths, which is why they
  survived to a real terminal.

`plan_dock.py` is deleted once the sidebar owns the section. `plan_strip.py`
stays — it is the collapsed surface, and the collapsed mode is unchanged.

## Data flow

The pane already funnels every ambient update through a small set of methods
on `ConversationPane` (`_bar()` at `tui/pane.py:1160` and its callers at
`1184`–`1207`, `1965`), which forward to `StatusBar`. The strips subscribe to
their own sources directly (`QueueStrip` → `QueueDigest`, `MonitorStrip` →
`MonitorManager`, both in `on_mount`).

The sidebar rides both, unchanged:

- for the pushed segments, the pane forwards to the sidebar on the same call
  it forwards to the bar;
- for the subscribed ones, the sidebar subscribes the same way the strips do.

**A hidden sidebar no-ops.** This is the discipline `PlanDock.refresh_plan`
uses today (`if self._open: self._paint()`) and `PlanStrip._tick` uses for
the spinner (repaint only while a task is actually running). The closed mode
therefore costs one branch per event, not a second render tree.

## The mode switch is CSS

`F3` adds or removes **one class** on the pane. The four collapsed surfaces
are hidden by rule:

```css
ConversationPane.-sidebar QueueStrip,
ConversationPane.-sidebar MonitorStrip,
ConversationPane.-sidebar PlanStrip,
ConversationPane.-sidebar StatusBar { display: none; }
```

Not five imperative `display = False` assignments. Five widgets toggled by
hand can drift out of sync with each other and with the sidebar; one class
cannot. It also means the collapsed mode is defined by the *absence* of a
class, so the untoggled pane is byte-for-byte today's pane.

## Width

Keep `PlanDock`'s numbers: `34%`, clamped to `26–60`.

- At **200 columns**: sidebar 60, transcript 140. Comfortable both sides.
- At **80 columns**: sidebar takes its 26-column floor, transcript drops to
  54 — 46 usable after `padding: 1 4`. Tight, and accepted. `fit_rows`
  degrades the tiered segments rather than truncating them, `F3` is a
  toggle, and the alternative designs (floor the transcript and let the
  sidebar give way; overlay the transcript entirely below a threshold) each
  add a second mode that exists only for a width that is rarely hit. The
  overlay is a real idea, but it is a dashboard *view* rather than a
  sidebar — a different feature, and it should get its own pass if it is
  ever wanted.

Two facts about width that must travel with the code, both already paid for
once in `plan_dock.py`:

- **`size.width` is 0 before layout.** A widget that was `display: none` has
  not been laid out, so the first paint after a toggle measures zero and
  falls back to the minimum, truncating far harder than the real width
  requires. `on_resize` repaints; the toggle paints too, and the resize is
  what corrects it.
- **`size` is already the content box.** Textual excludes padding from it,
  so `padding: 0 1` must not be subtracted again.

## Keybinding

`F3`'s binding label goes `Tasks` → `Dashboard` (`tui/app.py:279`).
`/tasks` keeps working and keeps toggling the same thing — it is now a
slightly narrow name for a wider surface, which is a smaller cost than
breaking a command someone has in their fingers.

## Testing

- **`fit_rows`** — tier selection, the drop case (narrowest tier still too
  wide), the exact-width boundary. Pure and cheap.
- **`render_sidebar`** — section order; an empty section omitted with its
  heading; degradation at 26 versus 60 columns. Assertions on plain text via
  the existing `strip_markup`.
- **The toggle** — assert the four collapsed surfaces are *actually not
  displayed*, by querying the widgets. **Not** by asserting the class is
  present: a class-name assertion passes against a CSS rule that no longer
  hides anything, which is precisely the bug the rule exists to prevent.
  Mutation-check it — break the rule, confirm the test goes red.

The last one is the test that matters. The first two are pure functions and
will be right; the toggle is where a green suite could ship a pane with a
status bar and a sidebar both on screen.

## Deliberately out of scope

- **The web client renders no sidebar.** Same call as the live task list
  (plan Task 12) and session titles — both TUI-first, both with the web half
  recorded as debt rather than quietly dropped. This lands in the same
  `TASKS.md` entry.
- **The overlay mode for narrow terminals** (option C above) — a different
  feature.
- **Reordering or restyling the collapsed mode.** Closed is today's pane.
