# The fleet dashboard: F10, and `aegis dash`

> **Status:** design, 2026-09-16. Brainstormed with Alex the same day.
> Every cost number here was measured before the design was written; the
> probe is `.playground/fleet-recap-probe/` and the index is at the end.

Nine agents were live on zion while this was written. Seeing what any one
of them is doing means opening its tab and scrolling. Seeing what *all* of
them are doing has no answer at all: no surface in aegis shows more than
one session's state, and the background workers that a queue spawns and
closes are invisible even in principle, because they are born and reaped
between two glances at the screen.

This is the view of the whole fleet. One card per session, no transcript:
what it is, how long it has been at it, which repo it stands in, what it
just finished, what it is doing right now, who is waiting on it, and who
made it.

## Two surfaces, one renderer

**F10** pushes the dashboard over the TUI. Escape or F10 closes it.
Clicking a card closes it and switches to that tab.

**`aegis dash`** is the same TUI booted with that screen already pushed,
for a second monitor. It needs no new client: `know-how/the-daemon.md`
establishes that *which tabs exist is brain state and is not per view*,
so a second client attached to the same daemon already holds every
session as a tab, and only focus, scroll and drafts are per view. Clicking
a card there switches that client's tab while the operator's own client
stays where it was.

The consequence for this design is that `aegis dash` is a boot flag, not a
program. All of its cost is in F10.

## A snapshot and a pure renderer

**Corrected 2026-09-16, after this spec was first committed.** The first
draft argued that `FleetSnapshot` had to be assembled in the brain and
shipped to clients as a frame, because a client-side assembly would break
the web client the way it broke the F3 sidebar. That argument is wrong and
the reason is worth recording, because it would have shaped the code.

Every view runs *inside* the daemon. The socket carries terminal bytes, not
application state: `know-how/the-daemon.md` says the terminal "pipes bytes
both ways", and the web design says a browser "gets a `View` exactly as a
tty does, and `aegis web` relays that view's frames to an xterm.js in the
page". There is no client that assembles anything, so there is nothing to
ship a frame to. The F3 sidebar's web gap had a different cause and the
daemon closed it. `--remote` — the one path that really did hold a
degraded manager and raise `RemoteUnsupportedError` — is **removed** by the
retire-web design.

So the split is ordinary decomposition, and the reason is testing. Assembly
reads the live `SessionManager` in memory; the renderer is a pure function
over a dataclass, which is how `render_sidebar`, `render_plan_strip` and
`render_repos` are already built and tested (`tests/test_sidebar_render.py`
constructs a `SidebarModel` and asserts on `.plain`, with no Textual app in
sight). A grid of nine cards with progress bars, truncation and three
states per card needs exactly that, and gets it for free by following the
pattern already here.

The renderer is pure, mirroring `render_sidebar`:

```python
render_fleet(snapshot: FleetSnapshot, palette, width: int) -> Text
```

New modules:

```
src/aegis/fleet/models.py     FleetSnapshot, CardView, BandView, Origin
src/aegis/fleet/snapshot.py   assembly from SessionManager
src/aegis/fleet/render.py     render_fleet, render_card — pure
src/aegis/tui/fleet_screen.py the Textual screen and its keys
```

Assembly touches live state only and never reads a transcript from disk:
`AgentSession`, `PlanTracker`, `SessionMetrics`, `repo_tracker`, the
monitor registry, the claims table, and the day's `CommsLedger`. The one
piece of new state is a bounded ring of the last five rendered event
headlines per session, maintained as events stream, because today the only
way to know what a session did a minute ago is to read its log.

## `Origin`: who made this agent, and whether it outlives its task

Today a session records one nullable string, `spawned_by`. It cannot
answer either half of the question.

| a session born from | recorded today |
|---|---|
| `/spawn` typed by the operator in a pane | `ctx.handle` — the pane, not the operator |
| `aegis_spawn` called by an agent | `from_handle` |
| `/fork` | `forked_by` |
| a queue worker | nothing |
| a workflow subagent | nothing |
| a group member | nothing |
| a tab the operator opens | nothing, correctly |

Two collisions. The last four rows are indistinguishable from each other:
`spawned_by=None` means "the operator's tab" and also "a queue worker" and
also "a workflow agent". And row 1 is indistinguishable from row 2: an
operator typing `/spawn` in tab A writes exactly what agent A calling the
tool writes.

The fix is a typed record, set at each of the six sites:

```python
@dataclass(frozen=True)
class Origin:
    kind: str = "operator"   # operator|agent|queue|workflow|group|schedule|fork
    by: str = ""             # pane handle, agent handle, queue name, workflow name
    detail: str = ""         # task id, workflow run id, group name
    returns_to: str = ""     # where the result goes, for queue callbacks
```

`spawned_by` stays as it is. It has three consumers already
(`close_guard`, `aegis_close`, the fork guard) and none of them wants a
new type; `Origin` sits beside it.

**Ephemerality is derived from `kind`, never stored.** Queue workers,
workflow subagents and group members die with their unit of work — a
queue worker is closed at `queue/manager.py:719` when its task completes.
Operator tabs, agent spawns and forks live until someone closes them. A
stored boolean would drift from the behaviour it names.

## The card

```
┌─ ⣾ une-tools-tasks ──────────── 4m12s ─┐
│ ordenar tareas y sincronizar el plan   │
│ opus · une-tools · main +3 ~2          │
│ plan ███████░░░ 7/10  ctx ████░░░░ 41% │
│ 1h47m · $2.14                          │
├────────────────────────────────────────┤
│ did  3 new tests in scheduler_test.py  │
│ now  closing the SIGERE pusher loop    │
├────────────────────────────────────────┤
│ 14:02 Edit apps/sigere/pusher.py       │
│ 14:02 Bash uv run pytest -k pusher     │
│ 14:03 Read know-how/deploying.md       │
├────────────────────────────────────────┤
│ ← une-demo-prep   ⛓ 2   ⏳ pytest 60%  │
└────────────────────────────────────────┘
```

The chrome is English like the rest of the TUI; the session title and any
quoted content are whatever that session is actually about.

No turn count: `SessionMetrics` has none, and adding a counter every other
consumer then has to keep correct, for a number the uptime and the cost
already imply, is not worth the field.

`did` and `now` are the two recap fields (below). The three middle rows
are the event ring. The footer carries edges from the `CommsLedger` (`←`
who last spoke to it, `→` who it is waiting on), held claims (`⛓`) and any
live monitor with its progress (`⏳`).

An **ephemeral** card reads differently. Its border is dimmed, it carries
a `⏱`, and the origin line is promoted to the top, because for a worker
the useful facts are who made it and where its answer goes:

```
┌┄ ⏱ brisk-babbage ───────────────── 38s ┄┐
│ queue general #a3f2 → fleet-dashboard-f10│
│ haiku · Workspace                        │
│ now  reading vault/Atlas/Know-how        │
└┄─────────────────────────────────────────┘
```

**A dead ephemeral card leaves a ghost for 60 seconds**, dimmed further,
showing what it did and where the result went. Without it, a worker that
lives 40 seconds appears and vanishes between two glances and the
dashboard lies by omission. This is the one decision here taken on
judgement rather than measurement: a fleet view in which background work
is invisible is a view of the operator's tabs, not of the fleet.

## The band

```
 AEGIS · zion · 9 agents · 6 yours · 3 ephemeral (2 queue, 1 workflow)  14:07
 2 working  6 ready  1 waiting        ctx avg 41%   worst kingly-karp 82%
 $18.42 live · recaps $1.20 (340)     queues 1/4    monitors 2
 repos  une-tools ×2 ⚠   aegis ×1   Workspace ×3   enciclopedia ×1
```

The repo line is the part that earns its space. `une-tools ×2 ⚠` says two
agents are standing in the same working tree, which is the condition that
actually costs an afternoon and which no surface in aegis shows today.

The cost is labelled `live`, not `today`: it is the sum over sessions open
right now, so it drops when a tab closes and counts a session started
yesterday in full. A real daily figure needs a ledger read, which the no-disk
rule keeps out of assembly.

Ghost cards are drawn but not counted: `total` and the four state counters
cover live sessions only, so while a ghost is on screen there is one more
card than `total` says.

The recap spend rides in the band on purpose: this design adds a recurring
paid call, and a paid call whose bill is not on screen is a paid call
nobody audits.

## The screen and its keys

Cards sit in **tab order, fixed** — card 3 is tab 3, so `Ctrl+3` lands
where the card says it will and existing muscle memory keeps working.
Movement is shown in colour and the spinner, never by reordering. Columns
are `max(1, width // 50)`.

| key | does |
|---|---|
| F10 / Esc | close |
| arrows | move the selection |
| Enter / click | close, switch to that tab |
| `1`–`9` | jump straight to that card's tab |

Redraw is driven by the event stream but coalesced at 500 ms, plus a 1 s
tick for clocks and spinners. Nine sessions streaming at once would
otherwise redraw the grid hundreds of times a second. The coalescing
window is a bench target, not a guess (see "What done means").

## The recap, and what it costs

### Measured first

Five arms, three repetitions each, `claude -p` haiku 4.5, CLI 2.1.270,
using the exact argv of `_oneshot_argv` (`--tools ""`,
`--setting-sources ""`, `--strict-mcp-config`, a two-field JSON schema).
Medians:

| arm | wall | in | out | thinking | cost |
|---|---|---|---|---|---|
| full window, thinking ON | 27.1s | 1,751 | 2,508 | 2,377 | $0.01583 |
| full window, thinking OFF | 4.7s | 1,573 | 103 | 0 | $0.00363 |
| minimal window, OFF | 3.8s | 1,135 | 103 | 0 | $0.00273 |
| empty window, OFF | 3.8s | 1,027 | 81 | 0 | $0.00241 |
| empty window, OFF, from `/tmp` | 3.9s | 1,016 | 82 | 0 | $0.00240 |

Three results change the design.

**The prefix floor is 1,027 tokens, not 7,749.** The `_oneshot_argv`
docstring records 21,445 → 7,749 measured on 2026-08-26. Same flags today
cost 1,027, a further factor of 7.6, most likely because the CLI shed its
own prefix between 2.1.220 and 2.1.270. The docstring is stale and must be
re-measured through the real aegis path, not through this probe's
reimplementation of it.

**The window is nearly free, so it gets the good one.** A full window —
the commit with its body, a ten-line plan, the in-flight turn in prose —
costs 546 tokens over the floor, $0.0012. Squeezing it saves nothing and
measurably degrades the line: the full arm produced *"modified pusher.py
to accept multiple push destinations and verified 14/14 pusher tests
pass"*, naming a file and a count.

**Thinking off is 4.4× cheaper, 5.8× faster, and — the part that matters
for a refreshing screen — predictable.** The three thinking-on runs took
8.7s, 30.4s and 27.1s and emitted 603, 2,853 and 2,508 output tokens to
write two sentences. Off: 3.9s, 4.7s, 5.9s, always 100–107 tokens. The
lines were no better on; they were terser and named fewer files.

### The shape that follows

A five-minute timer is the wrong instrument at this price. The question is
not how often to refresh but **which session needs a call at all**, and
there are two answers.

**An idle session needs none.** Its last turn recap already exists, was
generated when that turn closed, and says exactly what was last finished.
The card reads it.

**A working session needs one**, because no existing call covers the turn
*in flight*: the turn recap fires when a turn closes, and what the
dashboard wants is what is happening inside a turn that has not closed. It
fires 60 s into a turn and refreshes every 120 s while the turn lasts. A
four-minute turn is three calls, $0.011.

The schema becomes `{done, doing}` — one shape, three readers: the F3
sidebar line, the F10 card, and `/recap` on demand.

Gating, all three required: a client is watching that session, the turn
has run past `recap_after_s`, and `recap_interval_s` has elapsed since the
last one. Nobody watching, nothing paid. Config:

```yaml
fleet:
  recap: watched        # watched | on | off
  recap_after_s: 60
  recap_interval_s: 120
```

F3 watches one pane, so its marginal cost is one session's worth. F10 left
open all day with three agents working costs about $0.43/hour at list
price, less against the subscription pool, and the band shows the running
total.

### `MAX_THINKING_TOKENS=0`

`generate_detailed` calls `create_subprocess_exec` with `cwd` and no
`env`, so it inherits the daemon's. It gets an explicit `env` with
`MAX_THINKING_TOKENS=0` for the recap, `titlegen` and `/btw`.

`--effort` is not an alternative and the docstring should say so: it runs
`low` to `max` with no off, and `low` still emitted 133 thinking tokens to
write two sentences (measured 2026-09-13,
`vault/Atlas/Architecture/2026-09-13-style-prompt-arms-experiment.md`).
The environment variable is the only real switch.

**The loop judge is deliberately excluded** until measured on its own. It
decides whether a turn satisfied an instruction, which is the one of the
four calls where reasoning might earn its cost. Turning it off on the
strength of a writing-task measurement would be reasoning past the
evidence.

## Vertical slices

**VS1 — the thinking cut.** Measure the real aegis recap path on and off,
wire `MAX_THINKING_TOKENS=0` into `_oneshot_argv`'s subprocess env for
recap, titlegen and `/btw`, and correct the stale prefix numbers in the
docstring. Independent of everything below and it pays for itself at every
turn boundary of every session from the first minute.

**VS2 — the thin end-to-end path.** `FleetSnapshot`, `render_fleet`, the
F10 screen, click-to-tab. Cards built **only from data that already
exists**, plus `Origin`. No mid-turn recap; the `now` line is blank and
`did` reads the existing turn recap. Press F10, see nine cards, click
one, land in its transcript.

**VS3 — the mid-turn recap**, its gate, the `{done, doing}` schema, the
F3 line, and the spend counter in the band.

**VS4 — `aegis dash`**, the boot flag.

## What done means

Beyond `make check`:

- F10 opened against a daemon started *after* the change, with the real
  fleet live, a click on a card landing in that session's transcript.
- A queue worker spawned, watched appearing as an ephemeral card, and
  watched leaving a ghost — the origin path verified against a real
  worker rather than a synthetic one. Every `kind` in `Origin` is asserted
  against the site that sets it; a test that hardcodes the value it
  branches on verifies nothing.
- An `aegis bench` scenario for a nine-card grid under a live event
  stream. `AGENTS.md` requires bench for any claim about speed, and this
  is the most expensive screen the TUI will have.
- The recap cost re-measured through the real path, with the number in the
  docstring.
- `CHANGELOG.md` entry; `docs/` updated for the new config keys and
  `aegis dash`.

## Deferred

**A relations strip.** Who-waits-on-whom lives in the card footer. A
dedicated region was considered and cut: the nine live sessions have one
real edge between them, and a graph drawn from that is decoration. If it
comes back it should be a fixed row of conflicts — two agents in one repo,
a disputed claim, context over 80% — not a drawing of the graph.

**The loop judge's thinking budget**, pending its own measurement.

## Index of measurements

| number | where |
|---|---|
| the five-arm recap probe (2026-09-16) | `.playground/fleet-recap-probe/probe.log` |
| `--effort low` still thinks; thinking off on a writing task | `vault/Atlas/Architecture/2026-09-13-style-prompt-arms-experiment.md`, Result 5 |
| 21,445 → 7,749 prefix cut (2026-08-26, now stale) | `src/aegis/drivers/claude.py:341` docstring |
| recap gating on substrate movement | `docs/superpowers/specs/2026-08-26-aegis-turn-boundary-generation-design.md` |
