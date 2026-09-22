# F3 sidebar — rules, gauges, and a plan that cannot run away

**Status:** proposed 2026-09-22
**Scope:** TUI only. The open `F3` mode. Nothing about the collapsed mode
(`QueueStrip`, `MonitorStrip`, `PlanStrip`, `StatusBar`) is redesigned here,
which is the same boundary `2026-08-07-aegis-f3-side-dashboard-design.md` drew.

Extends that spec rather than replacing it: the sections, their order, and the
rule that an empty section renders nothing all survive. What changes is how a
row is drawn and how many rows a section is allowed to take.

## The problem

The column is full, and it was measured rather than guessed.

On a 40-row terminal `Sidebar` gets 37 rows of content: `TabBar` takes one, and
`SIDEBAR_PAD_Y` takes one at each end. `render_sidebar` fed a session with a
five-task plan, two queues, two monitors and two repos measures **35 rows** at
every width it supports (56, 40 and 26 all produce 35 — the sections wrap
nothing, they only pick narrower tiers).

Two rows of headroom. Alex reads the result as crowded, and it is: the panel is
at 95% of its height before anything interesting happens.

Three separate faults put it there.

### Thirteen of the 35 rows carry no information

Seven headings and six blank separators. The blank line is the only thing
separating one section from the next, so the chrome cost is two rows per
section boundary rather than one.

`heading()` renders `bold {palette.muted}`. Muted is dimmer than the
`palette.ink` rows underneath it, so the structure of the column is the
quietest thing in it. Scanning for `MONITORS` means reading past the monitor
rows to find the word above them.

### Five quantities are fractions and one of them draws a bar

| section | quantity | drawn as |
|---|---|---|
| `CONTEXT` | `ctx 142k (71%)` | text |
| `CONTEXT` | `quota 47%` | text |
| `PLAN` | `1/5` | text, in the heading's right slot |
| `QUEUES` | `●1/2` | text |
| `SYSTEM` | `cpu 34% ram 61% disk 82%` | text |
| `MONITORS` | `62%` | `▓▓▓▓▓░░░`, eight cells |

Every one of those is a proportion, which is the one thing a bar reads faster
than a number. The column is 56 cells wide at its default 34% and has the room.

### `CONTEXT` and `SYSTEM` are still status-bar rows

Both build `Segment` tiers and hand them to `fit_rows`, so they render as
dot-separated lines: `↑142k (38% cached) ↓8.2k · ctx 142k (71%) · $1.84 · ⚒ 34 ·
1:20 / 42:10`, `cpu 34% ram 61% disk 82%`.

That compression is correct in `StatusBar`, where `fit()` has one row and must
degrade by priority. In a column with thirty rows it is a habit rather than a
constraint. The `2026-08-07` spec said the fix for four strips competing for
one line each was to spend the vertical axis; two sections never got that
treatment, because they were ported over as `Segment` tuples and a `Segment`
tuple is a status-bar shape.

The deeper version of this fault is a data-path one, and it is what most of the
work in this spec goes to: `SidebarModel.metrics`, `.quota` and `.system` are
`tuple[str, ...]` of **already-rendered markup**. The sidebar never sees the
numbers. It cannot draw a bar for a percentage it was handed as the string
`"quota 47%"`.

### `PLAN` has no bound

`_plan` calls `render_plan_dock`, which emits one row per task plus a header
per subplan. A twenty-task plan is twenty rows, which pushes `QUEUES`,
`MONITORS`, `REPOS` and `SYSTEM` off the bottom of a 37-row column entirely.
The sections that get evicted are the ones ordered *below* plan by volatility,
so the eviction takes out the monitors and repos that were placed low precisely
because they are stable, not because they are unimportant.

### One rendering bug found while measuring

`_session` appends `now_line` as a plain `Text` with no indent, because
`fit_rows` would drop a recap sentence whose narrowest tier overflows. Rich
wraps it, and the continuation lands flush left:

```
now reading pane.py to
find where the recap lands
```

The second line reads as a new row of the section.

## The shape

Twenty-nine rows for the same session, twenty-eight when the recap line fits
without wrapping. Eight rows of slack on a 40-row terminal, with bars
throughout, and a `PLAN` section that stays six rows whether the plan has five
tasks or fifty.

```
── SESSION ─────────────────────────────── ✻ working
fix the eviction race
opus · high · local
LOOP  ███░░░░░░░░░░░░░  3/20
now   reading pane.py to find where the recap
      lands
── CONTEXT ──────────────────────────────────────────
CTX   ████████████░░░░  71%  142k/200k
QUOTA █████░░░░░░░░░░░  47%  ↻ 3h04m
COST  ↑142k ↓8.2k · $1.84 · 1:20
── PLAN ────────────────────────────────────────  1/5
      ███░░░░░░░░░░░░░  20%
●  parse the recap header                      0:42
◑  writing the streaming parser                1:20
○  wire the strip into the pane                   —
   +2 more
── QUEUES ───────────────────────────────────────────
build  █████░░░░  ●1/2 ○3 ✓5
review ░░░░░░░░░  ●0/1 ✓2 ✗1
── MONITORS ─────────────────────────────────────────
pytest        ██████░░░░  62%  ETA 1:40
docker build  ░░██░░░░░░  7:10
── REPOS ───────────────────────────────────────── 2
● aegis   main  ~3 ↑2  +180 -22   deep-dijkstra
· warden  main  ↓1
── SYSTEM ───────────────────────────────────────────
CPU ███░░░░ 34%   RAM ██████░ 61%
DSK ████████ 82%              Mon 22 Sep · 18:41
CWD …/repos/aegis · aegis 0.38.0
```

## Components

### `heading()` draws a rule, and the blank line goes

`heading(text, palette, width, right="")` grows a leading `── ` and fills to
the right edge with `─` on `palette.rule`, which is the field the themes
already carry for exactly this (`AegisColors.rule`, "subtle border on that
background"). The section name stays `bold`, but moves from `palette.muted` to
`palette.ink`: the name is structure and must not be dimmer than its contents.

`render_sidebar` then joins blocks with `"\n"` instead of `"\n\n"`. The rule is
the separator; a blank line on top of it is the belt and the braces.

Six rows bought, and the two faults about chrome are both answered by the same
change.

The right-hand counter keeps its slot but no longer floats fifty columns from
the word, because the rule now connects them.

`SESSION` uses that slot for `state_label`, which is how the section loses a
row. The label is variable and can be long (`✻ working… · ◐ thinking` is 24
cells), so the rule yields to it down to a floor of three rule cells; below
that the label falls back to a row of its own and the section is six rows
again. The floor exists so the heading never degrades into a word, a gap and a
label with no rule joining them, which is the shape this change is removing.

### Gauges come from `aegis/fleet/render.py`, not from a new module

The F10 fleet dashboard shipped this vocabulary on 2026-09-16 and it is tested:

| symbol | does |
|---|---|
| `bar(pct, cells, style, pal)` | `█` fill, `░` remainder on `pal.rule` |
| `sweep_bar(cells, frame, pal)` | indeterminate: a block moving one cell per frame |
| `_gauge(label, pct, value, style, cells, pal, *, value_style, tail)` | the whole row: `label bar value tail`, truncated to `cells`, never wider |
| `ctx_style(pct, pal)` | `pal.error` over 80, `pal.accent` over 60, else `pal.ready` |
| `_reset(seconds)` | `↻ 3h04m` |

`_gauge` renders `label bar value tail` in exactly one row, which is the
property that makes this redesign cost nothing: every gauge **replaces** a text
row rather than adding one.

Three of these are private today and gain a second caller, so they are promoted
in place: `_gauge` → `gauge`, `_reset` → `reset_in`, `_rows_of` → `rows_of`.
They stay in `aegis/fleet/render.py` rather than moving to a shared module,
because moving them would mean a new file whose only justification is that two
callers exist, and the fleet band is the older and larger caller. Revisit if a
third arrives.

Reuse matters here for a second reason beyond saving code. F10 has bars and
pressure colours and F3 has neither, so the two dashboards do not look like the
same program. Sharing the renderer is what fixes that, and it keeps them
fixed: a change to how a bar is drawn shows in both or in neither.

### The data path: values, not rendered strings

This is the real work. `SidebarModel` gains numeric fields **beside** the tier
tuples it already has, and the tuples stay, because `StatusBar` still consumes
them and the collapsed mode is out of scope. A section draws a gauge when it
has the number and falls back to its existing tier row when it does not, which
also means a remote pane with no stats degrades to today's behaviour rather
than to a blank.

| new field | type | source | already exists? |
|---|---|---|---|
| `ctx` | `ContextGauge \| None` | `Metrics` | new, see below |
| `quota_gauges` | `tuple[QuotaGauge, ...]` | `aegis.usage.quota.quota_gauges()` | yes — the app calls it for the fleet band |
| `stats` | `SystemStats \| None` | `aegis.tui.sysmeter.sample_system()` | yes — held on the app as `_system_stats` |
| `loop_status` | `dict \| None` | the core's loop status | yes — `{"iteration", "max_iterations"}`, already passed to `StatusBar.set_loop` |

`PLAN` and `QUEUES` need no new field: `PlanState.done` / `.total` and
`QueueView.running` / `.max_parallel` are already on the model.

**`ContextGauge`.** `Metrics.render_tiers` computes `ctx_pct` at
`metrics.py:285` and then bakes it into a markup string. Extract the
arithmetic into `Metrics.gauge() -> ContextGauge | None`, a frozen dataclass
of `(pct, live_tokens, window)`, and have `render_tiers` call it. The
percentage is then computed once for both callers instead of being recovered
from a string by the second one.

Three fields and no more, because those are the three that exist. There is no
turn counter on `Metrics` — the closest things are `tool_calls`, `turn_seconds`
and `session_seconds` — so the `COST` row is not a new render at all: it is
`metrics` tier T3, the narrowest one `render_tiers` already returns
(`↑142k ↓8.2k · $1.84 · 1:20`). The gauge takes the fraction; the leftover tier
takes the rest. It repeats the input-token count the gauge also shows, which is
the price of not inventing a fifth tier for one caller.

**Quota and system.** `AegisApp._quota_tick` already builds the tier tuple and
calls `active.set_quota(tiers)`; it also has the readings that
`quota_gauges(readings, now=...)` consumes for the band. `_tick` already
assigns `self._system_stats = stats` before rendering the tiers. Both push
sites gain one argument — `set_quota(tiers, gauges)` and
`set_system(tiers, stats)` — and the pane stores the second alongside the
first. No new sampling, no new timer, no second subscription. The `_tick`
comment already says the sample is app-side so "F3 gets the same tuple"; this
makes that literally true of the numbers too.

**Loop.** `Pane._on_loop_change` receives `state` and already calls
`state.status()`. It keeps that dict on `self._loop_status` next to the tiers
it keeps today.

### `PLAN` gets a window

`_plan` stops handing the whole `PlanState` to `render_plan_dock` and hands it
a window, plus a summary gauge:

1. A `gauge` row: `done/total` as a bar with a percentage.
2. The **current** task, with one completed task above it and up to two pending
   below. When no task is in progress, the window is the first three pending.
3. `+k more` on `palette.muted` when tasks fall outside the window, counting
   both directions.

Five rows for any plan. A plan under four tasks renders every task and no
`+k more`, so short plans look exactly as they do today.

Subplans are the one case that does not fit a fixed window: a fan-out with
three subagents has three nested headers before any task. Window each subplan
to its own current task only — one header row and one task row each — and let
the `+k more` count cover the rest. A fan-out's question is which subagent is
still grinding, and the current task of each answers it.

The windowing is a pure function over `PlanState` with its own test, kept out
of `render_plan_dock`. `render_plan_dock` has exactly one production caller —
`sidebar._plan` — so folding the window into it would in fact be safe for the
collapsed mode, which draws through `render_plan_strip` instead. It stays out
for the other reason: `render_plan_dock` has a contract asserted in
`tests/test_plan_render.py` covering its header and its `(no plan)` body, and
selecting which tasks to show is a different question from how a task row
looks. Two pure functions, each with one job, each testable without the other.

### `SYSTEM` merges its two static rows instead of dropping them

An earlier draft of this spec cut `cwd` and `build` outright, on the grounds
that they never change and cost two permanent rows. That was wrong twice over,
and the reversal is worth recording because the reasoning is the point.

The `2026-08-07` spec put them there deliberately: "the pair at the bottom is
the pair you go looking for rather than notice — which directory this aegis is
rooted at, and which build of it is running."
`tests/test_sidebar_system.py::test_the_open_sidebar_answers_where_and_which_build`
defends that decision, and its docstring names the question they answer: "the
two questions a stale checkout makes you ask, on screen instead of in a shell."
Cutting them would have reversed a documented decision and deleted the test
guarding it, for two rows.

And the two rows are not needed. The rest of this spec buys nine rows of slack
on a 40-row terminal. Spending two of them to keep information is the right
trade in a redesign whose whole complaint is that rows were being spent on
chrome.

So: `cwd` and `build` merge onto one row, `CWD …/repos/aegis · aegis 0.38.0`,
narrowing by `format_cwd`'s existing tiers first since the build string is the
shorter and less compressible half. One row saved instead of two, the decision
and its test both intact.

The three meters become two rows of gauges, two per row at 40 cells or wider
and one per row below that, using `rows_of(gauges, per_row)` from the fleet
renderer for the pairing. The clock keeps its place beside the disk gauge.

### One bar glyph, not two

`monitor_strip._bar` draws `▓`/`░` at a fixed eight cells;
`fleet.render.bar` draws `█`/`░` at a caller-chosen width with the empty half
on `pal.rule`. Two bars in one program is the fault this spec is trying to
remove, so `_bar` goes and `format_mon` calls `bar`.

That changes the collapsed `MonitorStrip` too, since `format_mon` is shared.
It is a glyph swap in a row whose layout is untouched, and having the strip
disagree with the sidebar about what a bar looks like is worse than the
diff. Called out here because it is the one change in this spec that lands
outside the open `F3` mode.

### The `now` line wraps with a hanging indent

`_session` indents the continuation to the width of the `now ` label, so the
wrapped remainder reads as part of the same row. Rich has no hanging-indent
option on `Text`, so the fold is explicit: wrap the recap at `width - 6` and
prefix every row after the first with six spaces. Kept in `_session` rather
than pushed into `fit_rows`, which is documented as a function that drops a
segment rather than wrapping it.

## Row budget

| section | now | after |
|---|---|---|
| `SESSION` | 6 | 5, +1 when the recap wraps |
| `CONTEXT` | 3 | 4 |
| `PLAN` | 6 | 6 |
| `QUEUES` | 3 | 3 |
| `MONITORS` | 3 | 3 |
| `REPOS` | 3 | 3 |
| `SYSTEM` | 5 | 4 |
| blank separators | 6 | 0 |
| **total** | **35** | **28–29** |

`SESSION` loses its state row to the rule's right slot and gains the recap's
continuation when the recap is long, so it is the one section whose height
depends on the data rather than on the section list.

`CONTEXT` gains a row: cost and turns move off the context line onto their own,
which is what lets the context line become a gauge. `PLAN` holds at six for
this five-task plan and stops growing past it.

The twenty-task plan that does not fit today renders in the same 29 rows.

## Testing

`render_sidebar` is pure and already has `tests/test_sidebar_render.py`,
`_repos.py` and `_system.py`. The new tests follow that shape — a model in,
assertions on `.plain` — and the three that matter are:

1. **The row budget is a test, not a hope.** A golden-ish assertion that a
   realistic full model renders at or under 30 rows at widths 56, 40 and 26.
   Without it the budget regresses the first time a section grows.
2. **`PLAN` is bounded.** A fifty-task plan renders the same row count as a
   six-task one, and the current task is inside the window.
3. **A gauge degrades to its tier row.** A model with tiers but no numbers
   (the remote-pane case) renders today's text rows and does not crash.

Plus: the rule heading fills to the exact width in cells, not characters (the
existing `heading` test already guards this for the counter and extends to the
rule); the `now` continuation is indented; `format_mon` draws `█`.

`make check` passing is not done, per `AGENTS.md`. This is a visual change, so
it is also exercised in the TUI against a daemon started after the change, at
40 rows, with a plan long enough to prove the window.

## What this deliberately does not do

**Collapsible or focusable sections.** At 27 rows inside a 37-row budget
nothing needs hiding, and it would add interaction state to buy nothing. If the
budget tightens later — a much longer repo list, a fan-out with six subplans —
this is the next thing to reach for.

**Sparklines.** `aegis/usage/render.py` already has `_BLOCKS = " ▁▂▃▄▅▆▇█"` if
history is wanted for cpu and ram later. A bar answers "is it hot now", which
is what the sidebar is asked.

**Anything in the collapsed mode**, with the single stated exception of the
`format_mon` glyph.
