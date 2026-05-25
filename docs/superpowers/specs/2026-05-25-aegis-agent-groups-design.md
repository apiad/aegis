# Agent Groups — design spec

**Date:** 2026-05-25
**Status:** Draft, approved
**Owner:** Alex + Claude

## Summary

`aegis` gains a third coordination primitive on top of queues and inboxes:
**groups** — a named bag of N agents addressable collectively. Spawned
agents can live solo (as today) or in a named group; a group exposes
**broadcast** (one message fans out to every member's inbox with a shared
correlation id), **`wait_all`** (block until every member's next turn
ends), and **`wait_any`** (block until the first finishes; surviving
members receive an inbox cancel they may honor or ignore). Results come
back as a typed `GroupResult` bundle, not a blob-of-strings.

The hierarchy is two-level only — agents | groups, no nesting. Groups
have mandatory semantic names. Membership is dynamic; persistence
mirrors the queue substrate (JSONL log, restart replay). The same
primitives are exposed three ways: MCP tools (for spawned agents),
`engine.*` methods (for Python workflows), and a TUI surface
(`Ctrl+T` / `Ctrl+Shift+T` / `Ctrl+G` plus a 2nd-row tab band for
members and a glance-dashboard on the group tab).

Source: Alex's 2026-05-25 01:39 voice recording. Prior-art survey at
`/home/apiad/Workspace/.playground/aegis-groups-prior-art.md`.

## Motivation

Aegis today ships three working coordination primitives:

- **Queues** — producer enqueues, worker spawned ephemeral, callback on
  completion. One-shot fan-out with no broadcaster-side coordination.
- **Inboxes** — per-handle delivery with wake-on-idle / mid-turn buffer
  / turn-end chain. Plumbing under everything else.
- **Handoff** (`aegis_handoff`) — peer-to-peer message between two
  existing agents.

What's missing is the **collective addressing** layer: ways to talk to
N persistent agents as one entity, and to synchronize on what they
return. The committee-of-reviewers pattern (security + style + logic),
the N-way speculative race (best-of-5), the parallel deep audit (4
agents same task), the structured supervisor (1 lead + 3 specialists)
— all are awkward today because there's no first-class group concept.
Each producer reimplements its own ad-hoc fan-out by chaining
`spawn` + `handoff` calls, and synchronization devolves to polling
inboxes.

The prior-art survey confirms the gap: Claude Code Teams ships a
similar primitive but with no real broadcast (forces N point-to-point
sends), an immutable lead, and no `wait_any`. LangGraph has `wait_all`
via supersteps but `wait_any` only as a manual race+cancel pattern.
CrewAI and AutoGen ship no `wait_any` at all. OpenAI Agents SDK
deliberately rejects groups in favor of handoffs-only. **True
broadcast with isolated per-recipient context, plus first-class
`wait_any` with explicit loser-cancel, is a gap aegis can fill.**

This spec defines:

1. The group model (membership, lifecycle, persistence).
2. Spawn primitives (atomic + sugars).
3. Broadcast and wait primitives with the four-field contract.
4. `GroupResult` and reducer model.
5. MCP surface.
6. Workflow Python API.
7. TUI surface (tabs, keybinds, glance dashboard).
8. Testing plan.

## Non-goals (v1)

- **Group-of-groups (nesting).** Two levels only — validated rejection
  per the prior-art survey. Almost every framework that allows nesting
  regrets it.
- **Auto-worktree-per-member.** Cursor and Claude Code Teams converge
  on git-worktree-per-agent for write-heavy parallel work; aegis groups
  don't get this in v1. Flagged as a future spec when groups start
  writing to disk routinely.
- **TUI-side ephemeral spawn-and-go.** Ephemeral groups (spawn, broadcast,
  wait, dissolve) ship as a workflow-Python sugar only. The TUI keeps
  the persistent model — Alex creates groups, broadcasts, gets results,
  broadcasts again.
- **`wait_n` (k-of-N).** YAGNI. Add when a real workflow needs it.
- **AegisServer extraction / rename.** The existing `SessionManager` +
  `AppBridge` already plays the kernel role; calling it `AegisServer`
  and publishing it as a stable embed surface is a separate spec.
  Groups layer on top of `SessionManager` directly.
- **Cross-host groups.** A group lives in one `aegis serve` process.
  Cross-host coordination is gated on the future daemon project.

## The group model

A **group** is a named bag of N **member** agents. Both group and member
have these invariants:

- **Names are mandatory and semantic.** No `group-1`, `group-2`. The
  TUI prompts on creation; MCP / workflow APIs error if name is
  missing or matches an existing live group.
- **Two levels.** An agent is either at the root or in exactly one
  group. No nesting.
- **Heterogeneous membership.** A group may contain agents of mixed
  profiles (`{architect: opus, editor: sonnet, qa: haiku}`).
- **Membership by reference, not duplication.** Spawn takes existing
  `.aegis.py` profile names — `Group(["security_reviewer",
  "style_reviewer", "logic_reviewer"])` instantiates three named
  profiles without cloning their config.
- **Empty-group lifecycle.** When the last member is removed, the
  group is auto-closed and its name freed. Recreating with the same
  name later is allowed (clean slate; no historical link).
- **Lead is implicit.** Unlike Claude Code Teams, aegis has no fixed
  "lead" role. Whoever broadcasts is the lead for that broadcast.
  Any agent (root or member of any group) can broadcast to any group
  it knows about.
- **Persistence.** Group membership and broadcast lifecycle are
  written to `.aegis/state/groups/<name>.jsonl`. On restart, groups
  are reconstituted; members whose sessions are lost become `lost`;
  in-flight broadcasts are marked `failed:interrupted`.

## Spawn primitives

Three forms, layered. The atomic primitive is always:

```python
spawn(profile: str, *, group: str | None = None) -> handle: str
```

When `group=None`, the agent lands at root (today's behaviour). When
`group="reviewers"`, the agent joins (or creates) that group.

Two sugars on top:

```python
# Uniform fan-out — N copies of the same profile in a new/existing group
spawn(profile: str, *, n: int, group: str) -> list[handle]

# Heterogeneous — explicit profile list, all in one group
spawn_group(name: str, profiles: list[str]) -> list[handle]
```

Both desugar to N atomic `spawn(profile, group=name)` calls. Profile
names must resolve in the loaded `.aegis.py`; unknown profile errors
fail-loud at the API boundary.

Plus a workflow-only **ephemeral** form for batch patterns
(Anthropic-research-system style):

```python
async with engine.ephemeral_group(profiles=[...]) as g:
    await g.broadcast(objective=..., ...)
    result = await g.wait_all()
    # g dissolved on context exit; members closed
```

The ephemeral group is invisible to the TUI tab bar; it appears in
the queue dashboard's `IN-FLIGHT` band like queue workers do.

## Broadcast and wait primitives

### Broadcast

```python
broadcast(
    group: str,
    *,
    objective: str,
    output_format: str,
    tool_guidance: str,
    boundaries: str,
) -> broadcast_id: str
```

The **four-field contract** is required (not optional). Survey
finding: this is the single highest-leverage prompt-shape pattern in
the literature (Anthropic multi-agent research system). Encoded in
the MCP tool schema so workflows can't omit fields. The four fields
are composed into a single inbox message per member with header:

```
> from group:<name>/broadcast:<id> · objective: <objective>
> output_format: <output_format>
> tool_guidance: <tool_guidance>
> boundaries: <boundaries>
```

Each member receives the message through its usual `InboxRouter`
delivery path; the body becomes the next user-message turn.

**Single in-flight per group.** A second `broadcast` to a group with
an open broadcast errors immediately with the open broadcast's id.
This keeps `wait_all`/`wait_any` semantics unambiguous in v1.

### Wait

```python
wait_all(group: str, *, timeout: float = 600.0,
         reducer: str | Callable = "concat") -> GroupResult

wait_any(group: str, *, timeout: float = 600.0,
         cancel_losers: bool = True) -> GroupResult
```

**Done-for-member-X** = the first turn-end after X received the
current broadcast. The captured result is X's final assistant text of
that turn — same convention queue callbacks already use, so member
agents don't need to learn a new tool.

- `wait_all` wakes when every member has posted one post-broadcast
  turn-end, or on timeout (returns partial result + `timeouts` list).
- `wait_any` wakes on the first member's turn-end. By default,
  surviving in-flight members receive an inbox message tagged
  `group:<name>/cancel:<broadcast_id>`:
  ```
  > from group:<name>/cancel · superseded by <winner_handle>
  ```
  They may honor it (next turn-end is a no-op) or ignore it (continue
  the turn). Setting `cancel_losers=False` skips the cancel signal —
  losers run to completion but their results aren't collected.

## `GroupResult`

```python
@dataclass
class MemberResult:
    handle: str
    text: str               # final assistant text of the post-broadcast turn
    turn_ms: int
    tokens_in: int
    tokens_out: int
    status: Literal["done", "canceled", "errored", "timeout", "lost"]

@dataclass
class GroupResult:
    broadcast_id: str
    by_member: dict[str, MemberResult]    # handle → result
    combined: Any                          # reducer output
    errors: dict[str, str]                 # handle → error text
    timeouts: list[str]                    # handles that didn't finish
```

**Default reducers** registered by name:

- `"concat"` — `"\n\n---\n<handle>: <text>"` joined across members
  in completion order. The default.
- `"join_by_handle"` — dict of `{handle: text}`.
- `"last_wins"` — just the last member's text.
- `"majority_vote"` — for short-string outputs only; counts identical
  trimmed strings and returns the modal one with tie-break = first
  finisher.

`reducer` may also be a `Callable[[dict[str, MemberResult]], Any]` for
custom aggregation (workflows only — MCP exposes only the named
reducers in v1 to keep the tool schema tractable).

## Lifecycle and persistence

Per-group JSONL log at `.aegis/state/groups/<name>.jsonl`. Event
types:

- `created(name, created_by, created_at)`
- `member_added(handle, profile, added_by, added_at)`
- `member_removed(handle, reason, removed_at)`
- `broadcast_started(broadcast_id, objective, output_format,
  tool_guidance, boundaries, sender, members)`
- `member_result(broadcast_id, handle, status, text_preview,
  tokens_in, tokens_out, turn_ms)`
- `broadcast_completed(broadcast_id, mode: "wait_all"|"wait_any",
  reducer, completed_at)`
- `renamed(old, new)`
- `dissolved(reason)`

On `aegis serve` start, `GroupRegistry.start()` replays each log,
reconstructs in-memory state, and:

- Marks any broadcast with a `_started` but no `_completed` as
  `failed:interrupted` and records a `broadcast_completed` event with
  that mode.
- Cross-checks each member handle against the live `SessionManager`.
  Handles whose sessions are gone become `lost` and produce
  `member_removed(reason="lost-on-restart")` events.
- Auto-dissolves any group left with zero live members.

## Maintenance operations

All exposed at MCP, workflow, and (where sensible) TUI:

- `rename_group(old, new)` — must not collide with an existing live
  group name.
- `dissolve_group(name)` — closes all member sessions; emits
  `dissolved(reason="user")`. Members do **not** revert to root.
- `move_member(handle, new_group_or_root)` — atomic move. If
  `new_group_or_root="<root>"`, the agent becomes a root agent.

## MCP surface

New tools, all under the `aegis_group_*` prefix:

| Tool | Purpose |
|---|---|
| `aegis_group_create(name)` | Create empty group (rare — usually implicit via `aegis_group_spawn`). |
| `aegis_group_spawn(profile, group, n=1)` | Spawn 1 or N members of `profile` into `group`. |
| `aegis_group_spawn_mixed(name, profiles)` | Heterogeneous spawn. |
| `aegis_group_broadcast(group, objective, output_format, tool_guidance, boundaries)` | Four-field broadcast; returns `broadcast_id`. |
| `aegis_group_wait_all(group, timeout, reducer)` | Returns `GroupResult` JSON. |
| `aegis_group_wait_any(group, timeout, cancel_losers)` | Returns `GroupResult` JSON. |
| `aegis_group_status(group)` | Snapshot: members + their state + current/last broadcast. |
| `aegis_group_dissolve(group)` | Close group + all members. |
| `aegis_group_rename(old, new)` | |
| `aegis_group_move_member(handle, new_group_or_root)` | |

All ride the **existing tagged-sender envelope** used by handoff and
queue callbacks. Inbox headers:

- Broadcast in: `> from group:<name>/broadcast:<id> · objective: …`
- Cancel signal: `> from group:<name>/cancel:<id> · superseded by <handle>`
- Result aggregation to broadcaster: `> from group:<name>/result:<id> · <reducer-output>`

## Workflow Python API

Mirrors MCP one-to-one on `engine`. The same primitives Alex writes
in `.aegis.py` workflows are the same primitives any agent calls over
MCP — one logical model, two surfaces.

```python
@workflow
async def code_audit(engine, branch):
    g = await engine.spawn_group("auditors", [
        "security_reviewer", "style_reviewer", "logic_reviewer",
    ])
    await engine.broadcast(g,
        objective=f"Audit branch {branch}.",
        output_format="markdown checklist",
        tool_guidance="Read-only; do not edit files.",
        boundaries="Stop after 20 file reads.",
    )
    result = await engine.wait_all(g, timeout=600, reducer="join_by_handle")
    await engine.dissolve_group(g)
    return result.combined

@workflow
async def best_of_n(engine, prompt, n=5):
    async with engine.ephemeral_group(profiles=["opus"] * n) as g:
        await engine.broadcast(g, objective=prompt,
            output_format="single answer, no preamble",
            tool_guidance="None.", boundaries="One turn only.")
        winner = await engine.wait_any(g, cancel_losers=True)
    return winner.by_member[next(iter(winner.by_member))].text
```

## TUI surface

### Tab bar

The existing horizontal tab bar gains a third tab kind:

- **Agent tab** — today's shape: `● <handle>`.
- **Group tab** — `▣ <name> [<active>/<total> <state-emoji>]`. State
  emoji aggregates members: ⏳ any busy, 🔔 broadcast complete + result
  unconsumed, ✓ all idle, ⚠ one or more errored, ⛔ one or more lost.
- Tabs intermix; `Ctrl+←/→` cycles tabs as today; landing on a group
  tab shows the **glance dashboard** (not a member pane).

### Glance dashboard (the group tab body)

When the focused tab is a group tab and no member sub-tab is
selected, the body renders:

```
▣ reviewers — 3 members (auditors profile · created 14:22)

┌─ Members ──────────────────────────────────────────────────────────┐
│ ✓ ada-knuth        idle   ·  last turn 18s · 1.2k tok              │
│ ⏳ lucid-hopper     busy   ·  tool: Read repos/foo/auth.py · 04:12  │
│ ⚠ wry-turing       errored ·  see tab for details                  │
└────────────────────────────────────────────────────────────────────┘

┌─ Current broadcast ────────────────────────────────────────────────┐
│ id   br-9f3a · started 14:30 (02:18 ago) · mode: wait_all          │
│ obj  Audit branch feat/auth for security regressions.              │
│ done 1/3 — waiting on lucid-hopper, wry-turing                     │
└────────────────────────────────────────────────────────────────────┘

┌─ Recent broadcasts ────────────────────────────────────────────────┐
│ br-7c11 ✓ 14:25  wait_all   3/3 in 01:42  reducer: join_by_handle  │
│ br-5d09 ⚠ 14:18  wait_any   1/3 (cancel)  winner: ada-knuth        │
└────────────────────────────────────────────────────────────────────┘
```

`↓` drills into the 2nd-row member sub-tabs; `←/→` there moves across
members. `↑` returns from a member sub-tab to the glance dashboard.

### Keybinds

| Binding | Action |
|---|---|
| `Ctrl+T` | New root agent (today's behaviour). |
| `Ctrl+Shift+T` | New agent in current group. If current tab is root, prompts for a new group name and creates the group around the current agent + the new one. |
| `Ctrl+G` | Open **lasso modal**: multi-select existing root agents + name field; creates a new group containing the selected agents. |
| `Ctrl+B` (group tab) | Open broadcast composer (four-field form). |
| `Ctrl+W` (group tab) | Dissolve group (with confirmation modal showing member count). |
| `Ctrl+R` (group tab) | Rename group (prompt). |

### 2nd-row member sub-tabs

When a member sub-tab is focused, the body renders that member's
`ConversationPane` exactly as a root agent's tab would today. The
sub-tab label carries live status: `● <handle> ⏳ 04:12 1.2k`.

## Renderer reuse

No new renderable types. Broadcast / cancel / result envelopes are
rendered by the existing tagged-sender block (`✉` chrome already used
for handoffs and queue callbacks). The glance dashboard is a new
Textual widget (`GroupDashboard`) under `src/aegis/tui/groups/`.

## File layout

```
src/aegis/groups/
  __init__.py
  registry.py       # GroupRegistry: in-memory state + JSONL log
  models.py         # Group, MemberRef, BroadcastRecord, GroupResult, MemberResult
  broadcast.py      # BroadcastTracker: single-in-flight per group, correlation
  reducers.py       # named reducers (concat, join_by_handle, last_wins, majority_vote)
  persistence.py    # JSONL writer/replay shared shape with queue substrate
src/aegis/tui/groups/
  dashboard.py      # GroupDashboard widget
  lasso.py          # Ctrl+G modal
  broadcast.py      # Ctrl+B four-field composer
src/aegis/mcp/server.py        # +aegis_group_* tools
src/aegis/workflow/engine.py   # +group/broadcast/wait_all/wait_any methods
tests/test_group_registry.py
tests/test_group_broadcast.py
tests/test_group_wait_all.py
tests/test_group_wait_any.py
tests/test_group_persistence.py
tests/test_group_tui_smoke.py
tests/test_group_live.py        # one live smoke (3-member roundtrip)
```

## Configuration shape (`.aegis.py`)

Groups are runtime constructs — they aren't declared in `.aegis.py`
the way queues are. But a couple of optional knobs help:

```python
groups = {
    "defaults": {
        "broadcast_timeout": 600,        # seconds
        "default_reducer": "concat",
    },
    "presets": {
        # Optional named factories: my_app.workflows:audit_team -> spawn_group
        "code_audit": {
            "profiles": ["security_reviewer", "style_reviewer", "logic_reviewer"],
        },
        "best_of_5": {
            "profiles": ["opus", "opus", "opus", "opus", "opus"],
        },
    },
}
```

A preset can be invoked via `aegis_group_spawn_mixed(name,
preset="code_audit")` as a shortcut. Presets are pure config sugar —
no schema validation beyond profile-existence checks.

## Testing

### Hermetic (target +60 tests)

- `test_group_registry.py` — create, add member, remove member,
  empty-group auto-close, rename collision, dissolve cascade.
- `test_group_broadcast.py` — fan-out to N inboxes, shared
  broadcast_id, single-in-flight guard rejects second broadcast.
- `test_group_wait_all.py` — happy path, partial timeout, all-error,
  member-lost mid-broadcast.
- `test_group_wait_any.py` — first-finisher wins, cancel envelope
  delivered to losers, `cancel_losers=False` skips cancel.
- `test_group_persistence.py` — restart replays state; in-flight
  broadcast marked `failed:interrupted`; lost members produce
  `member_removed` events.
- `test_group_reducers.py` — each named reducer; custom callable
  reducer.
- `test_group_mcp_schemas.py` — `aegis_group_broadcast` rejects
  missing four-field fields with a clear error.
- `test_group_tui_smoke.py` — Ctrl+T / Ctrl+Shift+T / Ctrl+G
  keybinds; lasso modal selects + creates; glance dashboard renders
  with stub group state.

### Live smoke

- `test_group_live.py` — spawns a real 3-member heterogeneous group
  against the configured default harness, broadcasts a trivial
  prompt ("Reply with the word HEARD."), waits all, asserts
  `GroupResult.by_member` has 3 entries each containing "HEARD",
  asserts `combined` is concatenated, asserts dissolve cleans up.

## Open questions deferred to the implementation plan

These are intentionally NOT decided here — the implementation plan
should resolve them in the order it hits them:

1. **Broadcast composer Ctrl+B vs slash command.** Ctrl+B is the
   default; if it conflicts with a Textual built-in, fall back to
   `/broadcast` in the input line.
2. **Cancel signal honor mechanism.** v1: cancel is a passive inbox
   message; member agents may ignore it. v2 could add an explicit
   `aegis_check_cancel` MCP tool the member can poll mid-turn.
3. **`aegis_group_status` schema.** Decide whether to return the full
   broadcast history or just current + last 5. Default: current + last
   5; `?limit=N` parameter optional.
4. **Dashboard rendering when group is empty.** Show "no members —
   `Ctrl+Shift+T` to add" placeholder vs auto-dissolve at first
   render. Default: placeholder; auto-dissolve only triggers when a
   member is removed, not when one was never added.

## Implementation rough sizing

Vertical slice 1 (thinnest end-to-end): `GroupRegistry` + atomic
`spawn(profile, group=name)` + `broadcast` (single in-flight) +
`wait_all` + default `concat` reducer + one MCP tool path + one live
smoke. Everything else (wait_any, sugars, ephemeral, TUI dashboard,
reducers, presets) layered on top in subsequent slices.

Estimate: ~1 day of focused implementation per Alex's pace heuristic
(no novel algorithms; pattern lift from the existing queue substrate).
The implementation plan will decompose into ~6–8 vertical slices.

## References

- Source recording: `vault/+/Inbox/Recording 20260525013906.md`.
- Prior-art survey:
  `/home/apiad/Workspace/.playground/aegis-groups-prior-art.md`.
- Adjacent substrate specs (patterns lifted):
  - `docs/superpowers/specs/2026-05-20-aegis-task-queue-design.html`
  - `docs/superpowers/specs/2026-05-21-aegis-queue-dashboard-design.html`
  - `docs/superpowers/specs/2026-05-22-workflow-catalog-design.md`
