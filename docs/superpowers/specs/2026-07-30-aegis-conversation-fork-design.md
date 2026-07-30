---
title: Conversation fork — a worker that already knows
date: 2026-07-30
status: design
---

# Conversation fork — a worker that already knows

Every agent aegis spawns starts cold. The MCP briefing says so in as many
words:

> The worker is a fresh agent with no context — write the payload as a
> self-contained prompt with everything it needs (goal, constraints,
> files to read, success criteria).

That sentence is not documentation, it is a workaround. Context transfer
is manual, lossy, and the single largest cost of delegating: the parent
has already read the files, already ruled out three approaches, already
knows which test is flaky — and none of that reaches the worker except
by being retyped.

**Fork transfers it for free.** A forked agent inherits the parent's
entire conversation and continues from it under a new handle, in a new
tab, as an independent peer.

## Scope

This spec is **only** the conversation. Explicitly out of scope:

- **Working trees.** Forking a conversation does not fork the checkout;
  a fork starts in the parent's `cwd`. Isolating N forks on one repo via
  `git worktree` is a genuine follow-on, and one with real workspace-shape
  constraints — see *Deferred*.
- **Group fan-out.** `aegis_fork_group` needs a decision the groups
  runtime can't currently express (below). VS1 ships a single fork.

## Mechanism

`ClaudeAgentOptions` takes `resume=<session_id>` together with
`fork_session=True`: load that conversation, then branch instead of
continuing it. This rides the driver specced in
`2026-07-30-aegis-claude-agent-sdk-driver-design.md`.

Driver support is uneven and the spec should not pretend otherwise
(probed on zion, 2026-07-30):

| driver | fork | note |
|---|---|---|
| `claude-code` | ✅ | **`claude --fork-session`** — *"When resuming, create a new session ID"* |
| `claude-sdk` | ✅ | `resume` + `fork_session=True` |
| `opencode` | ⚠️ not over ACP | `opencode run --fork` exists, but aegis drives `opencode acp`, and ACP v1 `loadSession` has no fork parameter |
| `gemini` | ❌ | ACP v1, same reason |
| `lovelaice` | not yet | `load_session` exists; fork would need lovelaice-side work |

**This changes the shape of the work.** `--fork-session` is a real flag on
the CLI aegis already drives, so fork is **not gated on the SDK driver** —
`ClaudeDriver.fork()` is the same three-line argv insertion as
`ClaudeDriver.resume()` (`argv[:2] + ["--fork-session", "--resume", sid] +
argv[2:]`). VS1 can ship against today's `claude-code` and pick up
`claude-sdk` for free when that lands.

The opencode row is worth recording precisely because it is not a flat
"no": the capability exists in the tool, aegis just doesn't drive the
surface that exposes it. If fork proves valuable, "run opencode through
`run --fork` instead of `acp` for forked sessions" is a real option rather
than an upstream request.

## Shape

### Driver seam

`fork` is a sibling of `resume`, and gets the same treatment:

```python
class HarnessDriver(abc.ABC):
    supports_resume: bool = False
    supports_fork: bool = False          # new

    def fork(self, agent: Agent, cwd: str, mcp_url: str,
             handle: str, session_id: str) -> HarnessSession:
        """Build a session branching from an existing conversation.

        Default raises — only fork-capable drivers override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support session fork")
```

Same signature as `resume`, same default-raises pattern, so
`tui/resume_plan.py`'s "which of these can be restored" logic has an
obvious analogue and the `supports_*` flags stay the honest capability
map they already are.

### What is new versus inherited

| | inherited from parent | fresh for the fork |
|---|---|---|
| harness conversation | ✅ the whole thing | |
| `Agent` profile (model / effort / persona) | ✅ by default | overridable, see below |
| `cwd` | ✅ | |
| aegis handle | | ✅ new, from `generate_name` |
| log id (`new_log_id`) | | ✅ new — a fork is a new conversation |
| `.aegis/state/sessions/<log_id>.jsonl` | | ✅ new file |
| inbox, claims, group membership | | ✅ none |

The log id matters. `9da13d0` established that a log id is minted once at
spawn and never changes, precisely because handles recycle and two
unrelated conversations sharing a file is the bug that buried 160
conversations. A fork is a **new conversation that shares a prefix** — it
gets its own id, and the prefix is not duplicated into it (the parent's
transcript stays the parent's; the fork's log starts at the fork point).

Provenance rides alongside the existing `spawned_by`: a new
`forked_from: {handle, log_id, session_id}` recorded on the child. It is
what makes the relationship legible in `aegis_list_sessions`, `Ctrl+R`,
and the fork banner.

**Per-fork overrides.** `_overlay_agent(base, *, model, effort, prompt)`
already exists for exactly this shape and is never persisted. Fork reuses
it verbatim — so "fork this conversation but continue at `max` effort",
or "fork it onto a cheaper model to grind out the boring half", cost
nothing extra.

### MCP surface (VS1)

```python
aegis_fork(from_handle: str, prompt: str,
           slug: str | None = None,
           model: str | None = None,
           effort: str | None = None) -> dict
```

Deliberately shaped like `aegis_spawn`: fire-and-forget, returns the new
handle, records provenance, does **not** collect output. The parent gets
results back the same way it does from a spawn — the fork
`aegis_handoff`s when done, or the parent handoffs it later.

`prompt` is the fork's first turn — the *divergence*. "You've read the
parser already; now try the Line API approach instead."

### Refusals

Fork is refused, with the reason named, when:

- the target has no `session_id` yet (nothing to fork — it has not
  produced its first `SystemInit`);
- its driver reports `supports_fork = False`;
- **the target is mid-turn.** Forking a conversation whose last turn is
  still being written branches from a torn state. `aegis_close` already
  establishes the house pattern here: refuse, and list every unmet
  condition at once rather than one per attempt.

### Forking a dead session

Fork needs a `session_id`, not a live process — the same thing `resume`
needs. So forking from a **closed** conversation works, and
`Ctrl+R` (which already lists every stored conversation and reopens via
`drv.resume`) is the natural second entry point: *reopen* versus *fork*
on the same row. Worth building in VS2 because it is nearly free once the
driver seam exists, and it turns the transcript archive into a set of
branch points.

### TUI

A forked tab opens with a banner naming its parent and the turn it
branched at — the same treatment resumed tabs already get. The tab bar
gets no new state; a fork is just a session.

## The cost, stated plainly

A fork's first turn pays the **entire parent conversation** as input
tokens. N forks pay it N times. This is the exact inverse of the benefit:
context transfer becomes free in effort and expensive in tokens.

Prompt caching softens it — the shared prefix should hit cache-read rates
rather than full input — but "should" is doing work in that sentence and
it needs measuring, not assuming. Two things follow:

- **VS1 records the number.** The fork's first `Result` carries
  `cost_usd` and `model_usage` (slice 5 of the driver-visibility arc);
  log it against the parent's context size so the ratio is known before
  anyone builds fan-out on top.
- **Fan-out designs must respect it.** "Fork 8 ways" against a 200k-token
  parent is a different proposition from spawning 8 cold workers, and the
  cheaper option is not always the fork.

This is the main reason group fan-out is deferred rather than shipped
alongside: it is the feature most likely to be expensive, and nobody has
the number yet.

## Deferred

**Group fan-out (`aegis_fork_group`).** The obvious composition — fork N,
register as a group, `wait_all` — does not work as-is.
`GroupRuntime.wait_all` requires an open broadcast
(`self.tracker.current(group)` raises `UnknownGroup` otherwise), and
`broadcast()` sends *one* objective to every member. Fan-out wants a
different angle per member. So this needs either per-member broadcast
content or a fork-specific collection path, and that is a design decision
with consequences for the groups substrate — worth its own spec, once
VS1 has proven the primitive and produced the cost number above.

**Worktree isolation.** For N forks mutating the same repo, conversation
isolation without filesystem isolation just means they collide faster.
The workspace shape constrains this hard: worktreeing the aegis *project
root* is wrong here (`repos/` is gitignored, so a worktree of the
workspace contains no repos at all; and `vault/` must never be branched —
the `vault-autosync` timer reconciles it on the main checkout every 10
minutes and would never see a branch). The workable form is a worktree of
**one repo under `repos/<name>/`**, sited at
`.aegis/worktrees/<handle>/<repo>/` so that `find_project_root()` still
walks up to the real `.aegis.yaml` and the fork keeps the right state dir.
That is a follow-on spec, gated on a real fan-out to point it at.

**Non-Claude drivers.** Revisit if ACP grows a fork verb, or when
lovelaice has a reason to.

## Slices

1. **`fork` on the driver seam + `aegis_fork`.** `supports_fork`, the
   `fork()` default-raises, the `claude-sdk` implementation, the MCP verb,
   the three refusals, `forked_from` provenance, the TUI banner. Done when
   a fork of a live tab answers a question using a file the parent read
   and the fork never did.
2. **Fork from history.** `Ctrl+R` offers fork beside reopen. Done when a
   closed conversation branches.
3. *(separate spec)* group fan-out, once the cost number exists.

## Testing

- Refusals are the interesting surface: no `session_id`, `supports_fork
  = False`, mid-turn. One test each, each asserting the *reason* reaches
  the caller — a refusal that doesn't say why is the failure mode.
- **The parent is unchanged by a fork.** Assert the parent's log file is
  byte-identical before and after, and that its `session_id` is
  unmoved. This is the invariant a fork could plausibly break, and it
  would break silently.
- Provenance round-trips through `state/event_codec.py` (it has
  legacy-record decode, so an older log missing `forked_from` must still
  load).
- The live test needs a real `claude` and is marked `live`. Use
  `-m "not live"` for the hermetic suite — never `-k "not live"`, which
  matches `live` as a substring.

## Open questions

- ~~**Does `claude -p` expose a fork flag?**~~ **Answered 2026-07-30: yes,
  `--fork-session`.** VS1 covers `claude-code` today; `claude-sdk` inherits
  it. See the driver table above.
- **Does the SDK's `fork_session` branch at the last turn only, or can it
  branch at an arbitrary point?** If arbitrary, "fork at turn N" becomes
  possible and the `Ctrl+R` surface gets considerably more interesting.
  Probe in VS1; do not design for it before the answer.
- **Cached or not?** Measure the fork's first-turn input cost against the
  parent's context size. Everything downstream depends on this number.
