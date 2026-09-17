# The fleet dashboard, v2: a list, a detail pane, and a screen that rotates itself

> **Status:** designed 2026-09-17 with Alex, through four browser mockups.
> Not implemented. Supersedes the card grid of
> `docs/superpowers/specs/2026-09-16-aegis-fleet-dashboard-design.md`; the
> data model, the recap costs and the ghost rules of that spec still hold.
> The approved mockup is
> `.playground/fleet-render/.superpowers/brainstorm/1761831-1789648257/content/layout-v4.html`
> (workspace playground, not in this repo).

## Why

The v1 grid is a wall of equal cards that cut every line to 46 cells. Alex's
verdict after using it: the recap is the useful part and it is truncated, the
band is three lines of crowded text, and nothing on screen reads as a gauge.
He also wants to leave it open on a large screen, where nobody presses keys,
and see it move to whatever just changed.

## What it looks like

Three regions, top to bottom and left to right.

```
CPU ██████░░░ 64%   RAM █████░░░ 56% 17.9/32G   DSK ███░░░ 32%   CTX ████░░ 47% avg
cc 5h ███░░ 38% ↻2h14m  cc wk ████░ 81% ↻Fri  oc 5h █░░ 12%  oc wk ██░ 27%  oc mo ████ 94%
zion · ✻ 2 working · ● 1 ready · ⧗ 1 waiting · ✗ 1 error · $41.20 live · recap $0.09/12 · queues 1/4 · monitors 1 · 01:12

┌ list (44%) ───────────────────────────┐ ┌ detail ──────────────────────────────────────┐
│● 1 fleet-dashboard-f10  ▇▇▇▇ 92% ◉2 4m│ │fleet-dashboard-f10          ● working 4m12s│
│  opus · aegis · main +2 ~5            │ │Fleet dashboard F10                           │
│  now Reviewing the card-restore       │ │opus · aegis · main +2 ~5 · up 19h · tab 1    │
│      handoff and running the full     │ │NOW      (full, wrapped)                      │
├───────────────────────────────────────┤ │DID      (full, wrapped)                      │
│⧗ 2 une-tools-tasks      ▇▇ 41%  3m    │ │MONITORS pytest -n auto ████░░ 60% 48s ETA 32s│
│  opus · une-tools · main ~1           │ │GAUGES   ctx · plan · turn                    │
│  did Filed four open items from the   │ │PLAN     ● ● ● ◐ ○                            │
└───────────────────────────────────────┘ │ACTIVITY last three tool calls                │
                                          │SPEND    $168.02 · 2 claims · ← peer          │
                                          └──────────────────────────────────────────────┘
↑↓ select  enter open tab  1-9 tab  esc/F10 close           auto · next in 12s
```

### The band

- **Row 1, host gauges.** CPU, RAM (with used/total), DSK, and the average
  context of the live sessions, each a bar filling its quarter of the width.
- **Row 2, quota gauges.** One bar per window of every provider aegis already
  polls, in `QuotaProvider.bar_windows` order: Claude `5h`, `wk`; OpenCode Go
  rolling, weekly, monthly. Each carries its percentage and a reset countdown.
  Colour follows `QuotaWindow.severity`: normal green, warning orange,
  critical red with a blinking percentage. A provider with no credentials is
  omitted, not drawn empty. The data is the last `QuotaService.current()`;
  it is not polled any faster for F10.
- **Row 3, counters.** Counts by turn attention (working, need you, error,
  review, waiting, done; see `2026-09-17-aegis-turn-attention-design.md`),
  live cost, recap spend, queues, monitors, the build and the clock.

Under 110 columns the detail stacks under the list; the band's rows wrap
their gauges two per line.

### The list

One item per live session, in tab order, then ghosts. An item is at least
three lines, and every line wraps instead of being cut:

1. attention mark (or the pulsing state glyph while working), tab number, handle, a small context bar with its percentage,
   the live monitor count (`◉2`), and the turn time (working) or idle age;
2. where: agent, repo, branch and churn, or, for an ephemeral worker, who made
   it and who gets the answer;
3. `now` in full while working, otherwise `did` in full; the command tail only
   when a session has neither.

The selected item has the accent border and a raised background. Ghosts keep
their dashed border and the "closed 12s ago" age.

### The detail

The selected session, in full, with sections in this order: header (handle,
attention mark and label, state, turn time), title, where and uptime, **NOW**, **DID**, **MONITORS**,
**GAUGES** (context with tokens, plan done/total, turn time against the
session's average), **PLAN** (every task with its glyph), **ACTIVITY** (the
last three tool calls), **SPEND · COORDINATION** (cost, claims, comms edges).
A section with nothing to say is omitted. The pane scrolls on its own when the
session has more than fits.

**MONITORS** lists every live monitor of that session from
`monitor_manager.snapshot(for_handle=...)`: description, a bar at `pct`,
elapsed and `eta_s`. A monitor with no progress condition (`pct is None`)
draws an indeterminate bar, a block that sweeps across it, and says "no ETA".

### Motion

Terminal cells have no opacity, so motion is frame-driven. Frames advance
every 0.5 s:

- **pulse**: a working glyph alternates between the state colour and muted;
- **blink**: the error glyph and a critical quota percentage alternate between
  visible and blank;
- **sweep**: an indeterminate monitor bar moves its block one step.

## Keys, and the rotation

- `↑` / `↓` move the selection and the detail follows; the list scrolls to
  keep it visible. Clamped, not wrapped, as in v1.
- A click on an item selects it. `Enter` opens its tab. `1`–`9` open a tab by
  number, as in v1. `esc` / `F10` close.
- `aegis dash` opens the same screen.

**Auto mode** is on when the screen opens with nothing chosen, and resumes
120 s after the last key or click. Any key or click turns it off and restarts
that clock. The footer says `auto · next in 12s` while it is on.

In auto mode the screen decides which session the detail shows:

- **Newness.** For each handle the rotator remembers what the detail last
  showed (a fingerprint: state, `did`, `doing`, plan done/total and current
  subject, the set of live monitor ids and their states). A session is *new*
  when its current fingerprint differs from the remembered one.
- **Priority** among new sessions: `needs_input`, then `error`, then a monitor
  that finished or failed, then `review`, then a new `did` or plan movement,
  then a state change, then a new `now` (categories from the turn attention
  spec). Ties go to the session shown longest ago.
- **Pace.** A session stays at least 20 s. When some other session is new, the
  switch happens as soon as those 20 s have passed, so a change is on screen
  within 20-30 s of the check that sees it. With nothing new, the rotator
  steps through the working sessions every 30 s; with nothing working, it
  holds.
- Showing a session records its fingerprint, so it is not new again until it
  changes again.

## How it is built

Rendering stays pure and testable, the way `render_card` is today; the screen
only composes widgets and owns the clocks.

| unit | holds | depends on |
|---|---|---|
| `fleet/models.py` | `CardView` gains `detail` fields (plan tasks, monitor rows, avg turn); `BandView` gains `SystemStats`-shaped numbers and `QuotaGauge` rows instead of pre-formatted tiers | nothing new |
| `fleet/snapshot.py` | fills the new fields in memory, as today; monitor rows per handle from `monitor_manager.snapshot(for_handle=)` | the manager |
| `fleet/render.py` | `render_band(band, pal, width, frame)`, `render_item(card, pal, width, frame, selected)`, `render_detail(card, pal, width, frame)`; bars and the three motions are helpers here | rich only |
| `fleet/rotation.py` | `Rotator`: `observe(snapshot, now)`, `touch(now)` for a key or click, `pick(now)` returning a handle or `None`, `countdown(now)` | nothing Textual |
| `tui/fleet_screen.py` | three widgets (band `Static`, list `VerticalScroll` of one `Static` per item, detail `VerticalScroll`), the 1 s snapshot clock, the 0.5 s frame clock, keys and clicks | the above |
| `tui/app.py` | `_fleet_system_row` hands raw `SystemStats` and `(provider, QuotaState)` pairs instead of formatted tiers | sysmeter, quota services |

The frame clock redraws from the snapshot it already holds and never rebuilds
it. The mid-turn recap keeps its v1 gate (`fleet.recap: watched`, every working
session while F10 is open), so a dashboard left on all day costs about $0.004
a minute per working session, as measured in the v1 spec.

## Testing

- `render_*` against fixed snapshots and frame numbers: a long `did` wraps and
  is never cut; frame 0 and 1 differ only in the pulsing, blinking and
  sweeping cells; a critical quota blinks and a normal one does not; an
  indeterminate monitor sweeps.
- `Rotator` with a fake clock: holds 20 s; prefers error over a new `did`;
  a shown session is not new until it changes; a key suspends auto for 120 s;
  with nothing new it steps through working sessions every 30 s and holds when
  none work.
- `FleetScreen` through Textual's pilot: arrows move the detail, `Enter`
  chooses the selected handle, a click selects, the footer countdown appears
  only in auto mode.
- The screen rendered to PNG with the `.playground/fleet-render/` shooters,
  with the quota and system sources faked (they call real accounts), at 150
  and 220 columns, and read against the mockup before anything is called done.

## Out of scope

- Per-session CPU and RAM. The host gauges are system-wide; attributing a
  harness's process tree is a separate change.
- Polling quotas faster than the existing service does.
- Any layout for narrow terminals other than the one above: under 110 columns
  the detail stacks under the list instead of beside it.
