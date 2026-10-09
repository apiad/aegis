# aegis: how long each plan item took, how long the session waited on you, and when it will finish

**Status: implemented, 2026-10-09** (issue #228), following
`docs/superpowers/plans/2026-10-09-plan-timing.md`. Designed with Alex in a
brainstorm in a session tab, from the plan as
`2026-10-08-session-attention-design.md` left it.

## What this delivers

A person glancing at the Fleet sees, for each session with a plan, how far along
it is and roughly how much work is left: `▰▰▰▰▱▱▱▱ 4/8 · ~12m`. The `now` row says
how long the current item has taken. The session's sidebar says how long each
finished item took, how much of the session's time was work and how much was
spent waiting on the person, and how much work is left. The spinner on the item
in progress turns while the agent is working and stands still when it is not.

The agent reports nothing new. Every number comes from timestamps the store
already holds.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Who measures | The fold, from the `ts` on every record | Refolding gives the same numbers, so they survive a restart. The `plan_update` handler only sees plan calls and cannot tell work from idle. An agent asked for durations would estimate them, badly, and could forget the field |
| What counts as work | Time inside a turn, and time between turns when the turn ended without handing back to the person | Waiting on a monitor, a queue task or a peer is part of doing the task (Alex, 2026-10-09) |
| What counts as idle | Time between turns when the turn handed back to the person | Only the person decides when they return, so no estimate can include it |
| How an item is recognised across updates | By its exact text, as `_did` already does | A reworded item starts its clock again. Matching reworded items is out of scope |
| What the ETA is based on | This plan's own pace | The plan history behind the workspace's `estimating-work` skill measures a different scale (whole plans in hours), and a session's pace is the best predictor of the same session |
| When the ETA shows | Once at least one item is done | With nothing done there is no pace to extrapolate |
| How the clock runs live | The browser adds `now - clock_at` to what the fold accrued | The fold only accrues on plan records and turn boundaries, so the standing does not change, and the meta is not rewritten, on every tool call |

## The measure

### Work and idle

The fold already sees where a turn opens (a `send` record, or the harness's echo
of a prompt or inbox message) and where it closes (the `Result`). Between turns
there is a gap. The fold decides the gap's class the moment the turn closes:

- **idle** if the turn handed back to the person: it carries a `turn_end`
  report, it failed, or the person interrupted it;
- **work** otherwise. An agent that ends a turn to wait on a monitor or a queue
  task is told not to call `turn_end` (`mcp.py`, the priming), so a turn without
  a report is one waiting on something other than the person.

Deciding at the close, not at the next opening, keeps the live clock right while
the gap is open, and keeps a gap a work gap when the person chats with the
session while its CI runs. The cost is that an agent that forgets `turn_end`
makes the following wait count as work. Compliance with `turn_end` is measured
in #178; if it proves poor, the live attention (`waiting` versus anything else)
can override the class of the open gap in the browser.

Time inside a turn whose `Result` never came (the process died, aegis
restarted) runs until the turn's last record; from there to the next opening is
idle.

Only time after the plan's first record counts. Time before the agent made a
plan belongs to no plan.

### Attribution to items

Work time accrues to the item that is `doing` at that moment. Work with no item
`doing` (between items, or with every item pending) accrues to the plan only,
not to any item: it is in the plan's total and so in its pace, but has no row.

A plan whose items share no text with the previous plan is a new plan: its
totals start from zero.

### The ETA

```
pace      = plan work_s / done items
remaining = pace × (items not done) − work_s of the doing item, at least 0
```

`remaining` is work time. It does not move while the session is idle, because
the work clock is stopped.

## The standing

`standing["plan"]` keeps its items and gains time, and a sibling key holds the
plan's clock:

```python
standing["plan"] = [{"text": "...", "state": "done", "work_s": 312.0}, ...]
standing["clock"] = {
    "work_s": 1380.0,   # the plan's work so far, items and between items
    "idle_s": 2460.0,   # the plan's idle so far
    "at": 1791566880.5, # ts the totals above were accrued to
    "running": "work",  # what is running since "at": "work", "idle" or ""
}
```

`running` is `work` inside a turn and in a work gap, `idle` in an idle gap, and
empty before the plan's first record. The `doing` item's live time is its
`work_s` plus `now - at` when `running` is `work`; the same addition gives the
plan's live totals.

The standing is persisted in the meta, so a card at boot needs no store
(DESIGN.md, boot reads meta files). Thirty items add a few hundred bytes. A meta
from before this change has no `clock` and no `work_s`; it reads as no time
measured, and nothing crashes.

`attention.py` passes `clock` and each item's `work_s` through on the card, next
to `plan_done` and `plan_total`. The pace and the ETA are computed in the
browser, because they move with `now`.

## What the browser draws

### The Fleet card

The faint `plan 4/8` in the footer becomes a segmented bar, the count and the
ETA:

```
▰▰▰▰▱▱▱▱ 4/8 · ~12m
```

The bar has one segment per item at a fixed width of about 48 px, so segments
narrow as items grow; at 30 items they are 1 px each. Done segments are filled,
pending ones empty. The `doing` segment pulses while the session's attention is
`working` and holds still otherwise. The ETA carries a `~` and is absent until
one item is done.

The `now` row gains the current item's live work time:

```
now  Run the browser tests · 6m
did  Wire the fold to the card
```

Idle is not on the card: the card's `ago` already says how long since anything
happened.

### The sidebar

The plan's heading carries the totals, and each item its time, right-aligned:

```
Plan 4/8 · 23m work · 41m idle · ~12m left
 ✓ Read the fold                     2m
 ✓ Add work_s to standing            5m
 ✓ Classify the gaps                 7m
 ✓ Wire the fold to the card         4m
 ● Run the browser tests             6m
 ○ Sidebar rendering
 ○ Changelog fragment
 ○ Open the PR
```

Done items show their `work_s`, the `doing` item a live clock, pending items
nothing: dividing the ETA among them would claim a precision there is none of.

The `doing` item's spinner rotates while the session's attention is `working`
and is frozen, as it always is today (`base.css`, `.plan .ic.work`), otherwise.
`prefers-reduced-motion` keeps it frozen always, as for every other spinner.

### Format and refresh

Durations read `<1m`, `6m`, `1h12m`; no seconds. The live numbers refresh on the
same timer that moves the card's `ago`.

The tab strip does not change: it carries the attention mark and the handle and
has no room for more.

## Testing

- The fold, on hand-built record sequences: an item's time across several
  turns; a work gap (turn without a report) and an idle gap (turn with
  `turn_end`, a failed turn, an interrupted one); work between items; a plan
  replaced by one with no shared text; a turn with no `Result`; a refold giving
  the same standing.
- A meta without `clock` boots to a card with no times.
- A browser test against a real `aegis serve` with the fake claude: the card
  shows the bar, the count and the ETA after an item is done; the sidebar shows
  per-item times; the `doing` spinner animates while the turn runs and not after.

## Out of scope

- An ETA from the history of other plans.
- Returning the ETA to the agent in `plan_update`'s answer.
- Recognising an item whose text the agent reworded.
- Idle on the card, and anything on the tab strip.
