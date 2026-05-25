# Agent Groups

A **group** is a named collection of agents that share one inbox-fanout
channel, one broadcast-in-flight slot, and a tagged-result protocol.
Groups give you the *committee* shape — one prompt, many parallel
answers, one structured result — without leaving the aegis MCP plane.

Where the four older primitives are:

| Primitive | One verb |
|-----------|----------|
| Inbox     | send context to a peer |
| Queue     | spawn a worker on demand |
| Canvas    | collaborate on a document |
| Terminal  | share a live shell |
| Workflow  | deterministic Python orchestration |

Groups add a sixth: **broadcast and gather**.

## The model

- A `Group` is identified by a unique `name` and carries an ordered list
  of `members` (each a `handle` + the `profile` it was spawned with).
- A group has at most **one in-flight broadcast** at a time. Each
  broadcast carries a `broadcast_id` and the four required fields:
  `objective`, `output_format`, `tool_guidance`, `boundaries`.
- Each member receives the broadcast on its inbox tagged
  `group:<name>/broadcast:<id>` and replies into the same channel.
- A waiter (`wait_all` or `wait_any`) collects replies, applies a
  reducer, and returns a `GroupResult`.
- Group state is persisted append-only to
  `.aegis/state/groups/<name>.jsonl`. Boot replays it so a restart
  picks up the live registry.

Groups nest at most one level deep — a parent group may contain agents
or sub-groups, but a sub-group cannot itself contain a group.

## The four-field broadcast

Every broadcast carries four labelled fields. They show up in each
member's inbox as a single envelope so members can read them as one
coherent ask:

- **objective** — what to accomplish.
- **output_format** — how the reply should be shaped (free text,
  bullet list, JSON schema reference).
- **tool_guidance** — which aegis MCP tools / built-in tools the
  member should reach for, and which to avoid.
- **boundaries** — what's out of scope.

Empty strings are allowed but the keys must all be present. Members
who reply outside `output_format` get reduced normally; reducers can
choose to drop malformed entries (e.g. `majority_vote` ignores
non-equal answers).

## wait_all vs wait_any

| | `wait_all` | `wait_any` |
|---|---|---|
| Returns when | every member has replied or timeout | first reply, then optionally cancels losers |
| Default reducer | `concat` | `concat` |
| Loser cancel | n/a | `cancel_losers=True` sends `group:<name>/cancel:<id>` |
| Use when | you need every voice | first answer wins; e.g. fastest analyst |

`wait_any` cancellation is **passive**: a member receives the cancel
envelope on its inbox at the next turn boundary. Members can listen
for it and abort their work; if they ignore it, the result they
eventually produce is dropped.

## GroupResult

```python
@dataclass
class GroupResult:
    broadcast_id: str
    by_member: dict[str, MemberResult]   # handle → result
    reduced: Any                          # reducer output
    metadata: dict                        # timing, counts, reducer name
    order: list[str]                      # arrival order (for tiebreaks)
```

Reducers (registered in `aegis.groups.reducers`):

- **`concat`** — `\n\n`-joined text of every member's reply.
- **`join_by_handle`** — `{handle: reply}` dict.
- **`last_wins`** — the last arrival's reply.
- **`majority_vote`** — most common reply by `Counter`; first finisher
  wins ties.

Custom reducers register via `register_reducer(name, fn)`. The signature
is `(by_member, order) -> Any`.

## MCP surface

All tools live on the same aegis MCP server every spawned agent sees.

| Tool | Purpose |
|------|---------|
| `aegis_group_spawn` | one member at a time into a (possibly new) group |
| `aegis_group_spawn_mixed` | many members at once; `profiles=[…]` or `preset="…"` |
| `aegis_group_broadcast` | send the four-field message |
| `aegis_group_wait_all` | collect all replies; reducer selectable |
| `aegis_group_wait_any` | first reply wins; cancels losers by default |
| `aegis_group_status` | snapshot: members, in-flight broadcast, recent results |
| `aegis_group_dissolve` | close every member session, drop the group |
| `aegis_group_rename` | rename a group; persists across replay |
| `aegis_group_move_member` | relocate a member between groups |

`aegis_group_spawn_mixed(preset="<name>")` looks the profile list up
from `.aegis.yaml`'s `groups.presets.<name>.profiles`. See
[Configuration](configuration.md#groups).

## Workflow Python API

Inside `@workflow`-decorated functions the engine exposes a
mirror of the MCP surface:

```python
@workflow
async def audit(engine, *, branch: str):
    handles = await engine.spawn_group("rev", ["sec", "style", "logic"])
    bid = await engine.broadcast("rev",
        objective=f"audit {branch}",
        output_format="bullet list of issues, severity-tagged",
        tool_guidance="prefer Read+Grep; avoid Bash",
        boundaries="report only; no patches")
    result = await engine.wait_all("rev", timeout=300.0,
                                   reducer="join_by_handle")
    return result.reduced
```

For one-shot committees that should evaporate after returning:

```python
@workflow
async def quick_review(engine):
    async with engine.ephemeral_group(profiles=["a", "b", "c"]) as g:
        await g.broadcast(objective=..., output_format=..., ...)
        return (await g.wait_all()).reduced
    # Group is dissolved automatically on exit (success or error).
```

The `ephemeral_group` context manager generates a random name, spawns
the members, and dissolves the group when the block exits — workflow
runs that should leave no trace.

## TUI surface

A group appears as a top-row tab labelled `▣ <name> [<active>/<total> <emoji>]`.

- `▣ reviewers [2/3 ⏳]` — three members, two busy, one broadcast in flight.
- `▣ reviewers [0/3 ✓]` — three members, all idle.
- The emoji aggregates member states: `✓` (all idle), `⏳` (any busy),
  `⚠` (any errored), `⛔` (any lost).

Inside a group tab, the dashboard renders three stacked panels:
**Members**, **Current broadcast**, **Recent broadcasts**.

The keybinds are scoped:

| Key | Action |
|-----|--------|
| `Ctrl+G` | open the lasso modal (multi-select root agents → form a group) |
| `Ctrl+B` | open the four-field broadcast composer (group tab only) |
| `Ctrl+W` | confirm-and-dissolve the focused group |
| `Ctrl+R` | rename the focused group |
| `Ctrl+T` | new agent tab |
| `Ctrl+Shift+T` | new group tab |

## YAML configuration

Inline in `.aegis.yaml`:

```yaml
groups:
  defaults:
    broadcast_timeout: 300
    default_reducer: join_by_handle
  presets:
    code_audit:
      profiles: [sec, style, logic]
```

Overlay per-preset in `.aegis/groups/<name>.yaml`:

```yaml
# .aegis/groups/code_audit.yaml
profiles: [sec, style, logic]
```

Inline + overlay collisions on a preset name are fail-loud — one source
of truth per entry, same rule as queues and schedules.

## File layout

```
src/aegis/groups/
  models.py        # Group, MemberRef, GroupResult, MemberResult
  registry.py      # in-memory map + tracker; event emits
  reducers.py      # concat, join_by_handle, last_wins, majority_vote
  runtime.py       # broadcast / wait_all / wait_any
  persistence.py   # JSONL log + read/write + replay
  wiring.py        # spawn_many / spawn_group sugars
  bridge.py        # GroupsBridge implementation
src/aegis/tui/groups/
  state.py         # GroupTabState + aggregate-state emoji
  dashboard.py     # GroupDashboard widget + render_dashboard
```
