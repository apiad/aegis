# The live task list — plan tracking as first-class session state

**Status:** approved, not yet implemented
**Date:** 2026-08-05

## The problem

Ask an agent to do something multi-step and aegis renders its plan as a
run of anonymous tool calls:

```
⏺ TaskCreate(Explore aegis plan-rendering context)
⏺ TaskCreate(Clarify design questions with Alex)
⏺ TaskCreate(Write and commit the design spec)
⏺ TaskUpdate(1)
```

That last row is not a typo. `render_shared.describe_tool` falls back,
for a name it does not know, to the first stringy argument
(`render_shared.py:98`). For `TaskUpdate(taskId="1", status="in_progress")`
that is `"1"` — so the single most informative event in the stream, *the
agent just started working on something*, renders as the character `1`.

The cause is narrow and checkable. `events.py` special-cases exactly one
tool name, `TodoWrite`, promoting it to the canonical `AgentPlan` event.
Claude Code has since replaced `TodoWrite` with a `TaskCreate` /
`TaskUpdate` / `TaskList` / `TaskGet` family, and none of those names
match, so they fall through to the generic `ToolUse` path with
`kind="other"`.

Three sources feed plans, and they do not agree on shape:

| source | shape | carries id |
|---|---|---|
| `TodoWrite` (legacy claude) | cumulative snapshot | no |
| ACP `AgentPlanUpdate` | cumulative snapshot | no |
| `Task*` (current claude) | **incremental delta** | only in the tool *result* |

The `Task*` result text reads `Task #1 created successfully: <subject>`.
The tool_use that created it carries no id at all. Any consumer that
wants a coherent plan must pair use with result.

Beyond rendering, plan state has no representation in the session core,
so it is invisible to the coordination plane: a peer agent calling
`aegis_list_sessions()` can see that `live-task-list` is `working`, but
not that it is 3/8 through a plan and currently clarifying design
questions.

## What we're building

Plan state becomes first-class session state, owned by `AgentSession`,
with the TUI, the web client, and the MCP coordination plane as equal
readers.

Two layers, because shape and time are different problems.

### Layer 1 — the parser normalizes shape

`events.py` learns the `Task*` family alongside `TodoWrite`, folding
deltas into a cumulative plan and emitting a full `AgentPlan` after each
mutation. Every source then produces identical events regardless of
whether it speaks in snapshots or deltas.

The id problem is solved with machinery the parser already has. It
stashes a pending `TaskCreate` keyed by `tool_call_id` and completes it
when the matching `ToolResult` arrives — the same use↔result pairing
`state.tool_diffs` already does to attach diffs to `Edit` results. The
accumulated plan lives on the same `state` object.

`PlanEntry` gains two fields:

- `id: str | None` — the `Task*` identifier; `None` for snapshot sources,
  which have no stable identity across revisions.
- `active_form: str | None` — the present-continuous label
  (`"Clarifying design questions"`) the task tools already supply, used
  by the strip when a task is in progress.

Only the two mutating members of the family fold into the plan.
`TaskList` and `TaskGet` are reads: they leave plan state untouched and
keep rendering as ordinary tool calls.

Nothing downstream changes. `render.py:_render_agent_plan`,
`event_codec`, `renderEvent.js:planHtml`, `render_html._plan_html`, and
`btw/window` all already consume `AgentPlan`. ACP is already wired at
`drivers/acp.py:263`, so **ACP support arrives as a consequence of this
design rather than as extra work.**

This layer alone fixes the rendering complaint.

### Layer 2 — a tracker adds time and identity

New module `src/aegis/plan/`:

- `models.py` — `PlanTask` (id, subject, description, active_form,
  status, `started_at`, `working_s`, `first_started_at`,
  `completed_at`) and `PlanState` (ordered tasks, `done`, `total`,
  `current`).
- `tracker.py` — `PlanTracker`, folding `AgentPlan` events together with
  turn-boundary signals into a `PlanState`.
- `snapshot.py` — `PlanSnapshot`, the small roll-up the coordination
  plane carries.

`AgentSession` owns one tracker, subscribed through the existing
`add_event_observer` / `add_state_observer` seams — no new plumbing in
the turn loop.

For snapshot sources with no ids, the tracker matches tasks positionally
and by subject text, which is what those sources already imply by
sending a full ordered list each time.

#### What the clock measures

**Working time**, not wall-clock: the tracker accumulates elapsed time
for a task only while the session is mid-turn.

A session idles. Send an agent off, a task goes `in_progress`, the turn
ends, and the operator is in another tab for forty minutes. Wall-clock
would report 41 minutes for one minute of work, and a task left
`in_progress` overnight would read `9:41:07`. Working time stays
meaningful when the operator walks away, and for an agent that never
idles mid-plan it equals wall-clock anyway.

Three rules at the edges:

- A task that never enters `in_progress` (created and immediately
  completed) displays `—`, not `0:00`, so "instant" and "never tracked"
  do not look alike.
- A task leaving and re-entering `in_progress` **resumes** its
  accumulator rather than restarting it.
- Time is folded from each record's `aegis_ts`, never from a live clock
  read. Replay therefore reconstructs identical numbers — `Ctrl+R`
  reopen, a restarted aegis, and the web history all agree. Turn
  boundaries are recoverable from the log, since `Result` closes a turn
  and `UserMessage` opens one.

## Surfaces

### `PlanStrip` — always on

`src/aegis/tui/plan_strip.py`, mirroring `tui/monitor_strip.py`: a pure
`render_plan(state, palette) -> Text` plus a `Static` subscribed to the
tracker, hidden via `display:none` when there is no plan. It joins
`QueueStrip` / `MonitorStrip` / `PendingStrip` in `ConversationPane.compose`.

```
tasks: ▓▓▓▓░░░░ 1/3 · ▶ Clarifying design questions 1:03
```

One line, no horizontal cost, and it is the only surface that works
unchanged on a phone.

### `PlanDock` — on toggle

`src/aegis/tui/plan_dock.py`. `ConversationPane.compose` wraps its
`#transcript` `VerticalScroll` in a `Horizontal`; the dock is the second
child, hidden by default and toggled by `F3` (`F2` is already the
ConfigPanel) and by a `/tasks` slash command, which gives the web client
the same toggle through the shared `commands.dispatch()` seam.

```
┌ transcript ──────────────────┬ tasks ────────────────┐
│ ⏺ Read(events.py)            │ ✓ explore ctx    4:12 │
│ ⏺ Bash(grep …)               │ ▶ clarify Q's    1:03 │
│ …                            │ ○ write spec       —  │
└──────────────────────────────┴───────────────────────┘
```

Subagent plans **nest** under the row that spawned them, collapsible.
`AgentPlan` already carries `parent_tool_use_id`, which is the same key
the TUI uses to route events into a `SubagentBox`. Nesting is what makes
a fan-out legible — it shows which of three parallel agents is still
grinding. A flat merge would lose the causal structure and is rejected.

The strip stays flat and top-level-only even when the dock nests: a
one-line "what is happening now" signal merging four agents' lists is
noise.

### Web

The strip ships in the web client too — `AGENTS.md` holds the TUI and
PWA as co-equal. The dock becomes a slide-over panel rather than a
permanent sidebar, because a permanent sidebar on a phone is not a
thing. `renderEvent.js` needs no change; it already renders `AgentPlan`.

### The coordination plane

This is the half that makes plan state worth putting in the core.

`SessionInfo` (`mcp/bridge.py`) gains `plan: PlanSnapshot | None`
carrying `done`, `total`, `current`, `current_working_s`, `updated_at`.
Because `aegis_list_sessions` is `dataclasses.asdict(s)` over
`bridge.list_sessions()` (`mcp/server.py:840`), **the field reaches
every peer with no change to the tool body.** A coordinator asking who
is free now also learns who is 3/8 through what.

`SessionInfo` is constructed in five places — `core/manager.py:428`,
`tui/app.py` (three sites), and `tui/remote_manager.py:326`. The field
defaults to `None`, so every site stays valid; the manager and app sites
populate it from the tracker, and the remote-manager site receives it
over the wire so `--remote` TUIs show peer plans too.

New MCP tool **`aegis_peer_plan(handle)`** is the drill-down: the full
annotated task list with per-task status and working time. The roll-up
answers *is that agent busy and how far along*; the drill-down answers
*which eight tasks*.

`TabBar` gains a compact `3/8` per tab. `GroupDashboard` feeds the
roll-up into its existing per-member `detail` slot
(`tui/groups/dashboard.py:45`), which is the coordinator's view of a
fan-out with no new widget.

## Testing

The layers split cleanly, and the seams are pure functions.

- **Parser** — a `Task*` delta sequence folds to the expected cumulative
  `AgentPlan`s; `TaskCreate`'s id is picked up from the result rather
  than the use; an unmatched result does not corrupt the plan; the
  `TodoWrite` and ACP snapshot paths still produce what they produce
  today (existing tests in `tests/test_agent_plan.py` must stay green
  untouched).
- **Tracker** — working time accumulates only across mid-turn intervals;
  an idle gap contributes nothing; re-entering `in_progress` resumes;
  a never-started task reports `None`, not zero. Driven with an injected
  clock and synthetic timestamps, no sleeping.
- **Replay equivalence** — the property that matters most: folding a
  recorded event log yields a `PlanState` identical to the one the live
  session held. This is the test that catches a stray `now()` creeping
  into the tracker.
- **Renderers** — `render_plan` is pure, so strip and dock output assert
  as text, following `tests/test_render_event.py`.
- **Coordination** — `aegis_list_sessions()` carries a populated `plan`
  for a session with one, `None` for a session without; `aegis_peer_plan`
  returns the full list and refuses an unknown handle in the wording the
  other peer tools use.

## Explicitly out of scope

Flagged so they are not silently absorbed:

- **The collapsed `SubagentBox` is broken.** Real, acknowledged,
  separate fix with its own spec. This design nests subagent plans in
  the dock, which is a different surface and does not depend on that
  fix.
- **`tool_progress` and `system/task_started` land as `Unknown`.** Both
  observed in a live session log. `tool_progress` in particular carries
  `elapsed_time_seconds` and would feed a per-tool progress signal.
  Adjacent, worth its own follow-up, not this.
- **Cross-tab aggregation.** Each pane's dock shows that pane's plan.
  Aggregating across sessions is what the groups dashboard is for, and
  it gets the roll-up through `detail`.
