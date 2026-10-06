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

Reach for a group when:

- a question has **multiple useful perspectives** — a PR audited by
  separate security, style, and logic reviewers;
- **the fastest answer wins** — race two analysts; the slow one is
  told to stop;
- you want **consensus or contrast across providers** — the same
  prompt sent to Claude, Gemini, and OpenCode, voted or aligned;
- you want **N candidates and pick one** — generate ten outlines,
  reduce to the best;
- you need a **panel of role personas** to react to a proposal — PM,
  engineer, designer reading the same RFC and replying in their
  voice.

Full code patterns in [Patterns](#patterns) below.

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

## Patterns

Five patterns cover almost everything groups are good for. Each one
maps to a different choice of waiter + reducer; the rest is just the
four-field broadcast.

### Multi-lens code audit

Three reviewers with distinct lenses — security, style, logic — see
the same PR. You want every voice, keyed by who said it, so a follow-up
agent can address each one separately.

```python
aegis_group_spawn_mixed(name="audit", from_handle="pm",
    profiles=["sec_reviewer", "style_reviewer", "logic_reviewer"])

aegis_group_broadcast(name="audit",
    objective="review PR #214 on branch feat/rate-limit",
    output_format="markdown bullet list, each item severity-tagged "
                  "(high/med/low) and file:line-anchored",
    tool_guidance="prefer Read + Grep; avoid Bash and Edit",
    boundaries="report only — no patches, no commits")

result = aegis_group_wait_all(name="audit",
                              timeout=300,
                              reducer="join_by_handle")
# result.reduced = {"sec_reviewer": "…", "style_reviewer": "…",
#                   "logic_reviewer": "…"}
```

**Reducer choice.** `join_by_handle` keeps reports addressable per
reviewer. Use `concat` instead when downstream wants one flat document.

### Fastest-answer race

Two analysts read the same question; whichever finishes first is the
answer; the loser is told to stop. Useful when a fast model is good
enough most of the time but you want a careful model in reserve.

```python
aegis_group_spawn_mixed(name="quick-lookup", from_handle="pm",
    profiles=["haiku_fast", "opus_careful"])

aegis_group_broadcast(name="quick-lookup",
    objective="what does worker.py:_run_turn return on harness error?",
    output_format="one-paragraph answer plus the relevant code excerpt",
    tool_guidance="Read + Grep; one file only",
    boundaries="no edits, no other files")

winner = aegis_group_wait_any(name="quick-lookup",
                              timeout=120,
                              cancel_losers=True)
# winner.reduced = "<the fastest reply>"
# winner.metadata["winner_handle"] = "haiku_fast" (most of the time)
```

`cancel_losers=True` (default) sends a `group:<name>/cancel:<id>`
envelope to every loser at the next turn boundary. The cancel is
**passive** — well-behaved agents abort; agents that ignore it
finish anyway and their reply is silently dropped.

### Cross-provider consensus

The same prompt to Claude, Gemini, and OpenCode. Vote on the answer;
if they agree, ship; if not, you have evidence to look closer.

```python
aegis_group_spawn_mixed(name="oracle", from_handle="pm",
    profiles=["claude_opus", "gemini_3", "opencode_kimi"])

aegis_group_broadcast(name="oracle",
    objective="is the regex r'^\\d{4}-\\d{2}-\\d{2}$' anchored?",
    output_format="exactly one of: ANCHORED | UNANCHORED | AMBIGUOUS",
    tool_guidance="answer from knowledge; no tools needed",
    boundaries="single token only — no prose, no quoting")

verdict = aegis_group_wait_all(name="oracle",
                               timeout=60,
                               reducer="majority_vote")
# verdict.reduced = "ANCHORED" (if ≥ 2 agreed)
# verdict.by_member shows each model's literal reply for audit
```

**Reducer choice.** `majority_vote` ignores entries that don't share a
common value, so a malformed answer doesn't poison consensus. First
finisher wins ties — surfaced in `result.order`.

### Generate-and-pick (N candidates → one)

Ask the same group for N different attempts at the same artifact
(outline, function signature, error message), then keep the best by a
custom criterion.

```python
from aegis.groups.reducers import register_reducer

def longest_with_examples(by_member, order):
    candidates = [r.text for r in by_member.values()
                  if "example:" in r.text.lower()]
    return max(candidates, key=len, default="")

register_reducer("longest_with_examples", longest_with_examples)

# spawn five attempts at the same prompt
aegis_group_spawn_mixed(name="outlines", from_handle="editor",
    profiles=["writer", "writer", "writer", "writer", "writer"])

aegis_group_broadcast(name="outlines",
    objective="outline a 3000-word essay on cache invalidation",
    output_format="markdown headings with one-sentence summaries; "
                  "include at least one concrete worked example",
    tool_guidance="prose only; no tools",
    boundaries="≤500 words; no bibliography")

pick = aegis_group_wait_all(name="outlines",
                            timeout=240,
                            reducer="longest_with_examples")
# pick.reduced = the longest outline that contained "example:"
```

**Reducer choice.** Custom reducers receive `(by_member, order)` and
return anything. `order` lets you tiebreak by arrival time;
`by_member` carries the full `MemberResult` (text, timing, status).

### Role-persona panel

A panel of role personas reacts to the same RFC. Each agent runs the
same model but with a different `system_prompt` profile — PM looks at
scope, engineer at feasibility, designer at UX. You want each voice
intact, in role.

```python
aegis_group_spawn_mixed(name="rfc-panel", from_handle="author",
    profiles=["persona_pm", "persona_eng", "persona_ux"])

aegis_group_broadcast(name="rfc-panel",
    objective="react to RFC-014 (attached) from your role's lens",
    output_format="three sections — risks, what you'd cut, "
                  "what you'd add — bullet lists, ≤120 words each",
    tool_guidance="Read the RFC file; no other tools",
    boundaries="speak in your role's voice; no general engineering "
               "advice that ignores your role")

panel = aegis_group_wait_all(name="rfc-panel",
                             timeout=300,
                             reducer="join_by_handle")
# panel.reduced = {"persona_pm": "…", "persona_eng": "…",
#                  "persona_ux": "…"}
```

This pattern composes well with [Canvas](canvas.md): publish the panel
into a shared markdown file so the original author and a follow-up
synthesizer can both read it without re-asking.

### Ephemeral committees from a workflow

Inside a workflow, `engine.ephemeral_group(profiles=[…])` spawns the
members, runs the broadcast, returns the reduced result, and dissolves
the group on exit — successful or not. No name to garbage-collect, no
state file left behind.

```python
@workflow
async def quick_consensus(engine, *, question: str) -> str:
    async with engine.ephemeral_group(
        profiles=["claude_opus", "gemini_3", "opencode_kimi"]
    ) as g:
        await g.broadcast(
            objective=question,
            output_format="one of: YES | NO | UNCLEAR",
            tool_guidance="answer from knowledge; no tools",
            boundaries="single token only")
        return (await g.wait_all(reducer="majority_vote")).reduced
```

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
