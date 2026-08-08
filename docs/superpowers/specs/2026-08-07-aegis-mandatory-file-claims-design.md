# Mandatory file claims

**Status:** design approved; implementation plan at
`docs/superpowers/plans/2026-08-07-aegis-mandatory-file-claims.md`
**Date:** 2026-08-07
**Supersedes nothing.** Extends the claims primitive in `src/aegis/locks/`
(seventh coordination primitive) from advisory to enforced.

## Problem

`src/aegis/locks/` is a pure advisory board. `ClaimRegistry.claim()` returns
`(claim, granted, overlaps)`, and the *only* consequence of `granted: false`
is that the claim is not recorded — nothing stops the agent from writing
anyway. The enforcement lives entirely in the prose of the `aegis_meta`
briefing ("coordinate — don't barge in"). That is a norm, not a mechanism,
and norms are exactly what a careless agent drops when it is three tool
calls deep in a refactor.

Two failures follow from it:

- **Peers clobber each other's in-flight work.** The holder finds out at
  `git diff`, hours later, if at all.
- **The board is only as accurate as the agents are disciplined.** An agent
  that never calls `aegis_claim` is invisible on it, so the board under-reports
  precisely the sessions most likely to cause a collision.

## Threat model

**The careless agent, not the adversarial one.** The target is an agent that
would honor the rule if it were reminded at the moment it mattered, and that
reaches for `sed -i` out of habit rather than evasion. We are not defending
against a model that is actively trying to get around the gate.

This choice is what makes the design affordable. Genuinely making an agent
*incapable* of writing would require a kernel-level enforcement point, and the
dynamic nature of claims rules out the cheap options:

- **Landlock** is disqualified outright — a ruleset can only ever restrict
  further, never re-grant, so an agent that claims a path mid-session could
  never write to it.
- **Per-agent uid + POSIX ACLs** is dynamic and total, but needs root for uid
  allocation and collides with Alex's editor and the `vault-autosync` timer
  writing the same tree.
- **A per-agent FUSE view** resolving policy against `ClaimRegistry` on every
  open-for-write is the conceptually clean total answer, and costs a mount per
  session, a tax on every read, and a new class of "why did my build hang".

All three are deferred. If the careless-agent gate proves insufficient in
practice, FUSE is the escalation path and this design does not foreclose it —
the PDP boundary below is exactly the seam a FUSE server would consult.

### Non-goals

- Defending against deliberate evasion.
- Covering writes by non-aegis processes (Alex's editor, timers, `git`).
- Cross-host enforcement beyond what `Claim.host` already scopes.
- Any change to the grant rule in `ClaimRegistry.claim()`.

## Architecture: one PDP, three PEPs

`ClaimRegistry` becomes the **policy decision point**, unchanged in its
overlap and grant logic. Enforcement points go where aegis already sits on
the write path:

| PEP | Seam | Covers |
|---|---|---|
| ACP drivers | `AcpSession.request_permission()` (`drivers/acp.py:276`) and `write_text_file()` (`:284`) | gemini, opencode, lovelaice file tools |
| Claude Code | a `PreToolUse` hook injected via `--settings` in `ClaudeDriver.build_argv()` (`drivers/claude.py:234`) | Write, Edit, NotebookEdit |
| Bash | the same `PreToolUse` hook, inverted rule (below) | shell writes, best-effort |

For ACP the check is a precondition on a function aegis already owns —
`write_text_file` literally performs the write itself. For Claude Code,
`build_argv` already injects `--mcp-config` and `--append-system-prompt`, so
`--settings` is one more flag on a list that exists. Remote hosts work
unchanged: `hosts/connection.py` already reverse-tunnels the MCP port, so the
hook has an address on the far side.

### The Bash rule inverts

For Write/Edit the hook denies unless allowed. For Bash it **denies only on a
positive match** — a literal redirect target (`>`, `>>`), or a `sed -i` /
`tee` / `mv` / `cp` destination that resolves inside a foreign claim. A command
that fails to parse **passes**.

This is deliberate and it is the honest trade. Any static analysis of a shell
command is a guess; a deny-by-default guess makes Bash unusable within one
turn. Bash is porous under this design and the spec says so out loud rather
than implying a completeness the mechanism does not have.

### Enforcement domain

The check runs on the **resolved absolute path**, and applies **only within the
project root subtree**. A write to `/tmp`, to `~/.cache`, or to `vault/` from a
session rooted at `repos/aegis` is outside the domain and passes untouched.
Without this rule every agent bricks on its first scratch file.

Symlinks and `..` are resolved before the claim lookup; a path that resolves
outside root is outside the domain regardless of how it was written.

## Write policy

Five lines, in order:

1. **Unclaimed path** → auto-claim `shared` for the writer, write, silent.
2. **Writer's own claim** → write, silent.
3. **Foreign `shared` claim, non-destructive op** → shadow-copy, write, notify
   writer and holder.
4. **Foreign `shared` claim, full overwrite of an existing file** → **deny**.
   The escape is `aegis_claim` on that path.
5. **Foreign `exclusive` claim** → **deny**. No escape but negotiation.

### Why the destructive case is singled out

Not all writes into a shared claim are equally risky:

- `Edit` carries an `old_string` and **fails on its own** if a peer changed
  the region. Self-protecting; a notice is enough.
- Creating a **new file** inside someone's shared prefix is harmless.
- `Write` (full overwrite) of an **existing** file is the actual clobber
  vector, and the only one.

Putting the friction exactly there — and nowhere else — means the interstitial
fires rarely enough that agents never learn a reflex for it. A blanket deny on
every write into a shared claim would be routed around within one turn.

### The acknowledgment is `aegis_claim`

A Claude `PreToolUse` deny returns a reason string and the model retries;
there is no protocol slot on `Write` to carry a "yes, I know" flag. So the
acknowledgment must be out of band — and the out-of-band call that already
exists is `aegis_claim(paths, intent="shared")`, which **always succeeds**
against another shared claim (shared ∩ shared coexist, per the grant rule in
`registry.py`).

So the way an agent says "yes, I know this is shared, I am going in" is to
**join the shared claim itself**. No new primitive, no new flag, and the escape
hatch produces exactly the bookkeeping we want: two names on the same path,
visible to both holders and to Alex. It is also not satisfiable without reading
the deny message, since the agent must name the path.

This resolves the auto-claim tension: **auto-claim fires only on unclaimed
paths.** If a foreign claim is already present, the claim must be explicit.
That single rule carries the whole policy.

### The deny message is the deliverable

Under a careless-agent model the wall is not the point; the message is. A
careless agent does not know it is being careless. The message must name the
holder, the claim, and the way out:

```
denied: src/aegis/tui/ is exclusively claimed by lucid-knuth
(claim 01KZ…, since 14:02 — "refactoring the sidebar").
aegis_handoff lucid-knuth to negotiate, wait for release,
or narrow your edit.
```

Server-side, every denial is logged as a violation attempt. That log is the
second deliverable: it is how we find out which agents drift, where, and
whether the norm in the `aegis_meta` briefing is doing any work at all.

## Auto-claim

The PEP records a `shared` claim on behalf of any agent writing to an
unclaimed path inside the domain. The agent is never blocked and never has to
remember anything, and the board becomes **accurate for free** — which is the
thing actually broken today.

`Claim` gains an `auto: bool = False` field. Auto-claims differ from explicit
ones in three places and nowhere else:

- they never count toward the `aegis_close` refusal (below);
- they are rolled up rather than listed in the sidebar (below);
- they are always `shared`, never `exclusive`.

Auto-claims are otherwise ordinary claims: same overlap logic, same
persistence, same reaping.

**Invariant:** the board holds **live state only**; the append-only JSONL under
`.aegis/state/locks/` holds **history**. Degradation and reaping never lose the
record of who touched what. "Who wrote this file at 14:02" is a question for
the log, not a reason to keep a stale claim alive.

## Liveness: gone, live, dormant

Today `_prune_dead()` reaps any claim whose handle is not in `live_handles()`,
and it runs on every `claim()` and `active()`. But `live_handles` is **tab
existence**:

- `core/manager.py:460` — `{s.handle for s in self._sessions}`
- `tui/app.py:428` — `{p.handle for p in self._panes if isinstance(p, ConversationPane)}`

That is fine for an advisory board and exactly wrong under enforcement, because
agents in aegis characteristically **finish and sit there**. A session that
completed three hours ago, or whose harness subprocess died, or whose `/loop`
ended, is fully "live" by this predicate — and its exclusive claim on `src/` is
a permanent wall. The recourse the deny message offers goes to a handle that
will never answer. That is a deadlock, and the careless agent will do the
careless thing: give up, or find another way to write the file.

Three states replace two:

| State | Definition | Effect on claims |
|---|---|---|
| **Gone** | no session object | reaped (today's behavior, unchanged) |
| **Live** | mid-turn, or has a future | fully enforced |
| **Dormant** | session exists, no turn in ~20 min, nothing pending | **exclusive claims degrade to shared** |

"Has a future" is **already written**: it is the refusal condition on
`aegis_close` — not mid-turn, no live monitors, no pending reminders, nothing
undelivered in the inbox, no queue task running, no armed loop. One definition
of "this agent still has a future", shared by close and by locks.

### Degrade, not delete

The board still reads "`lucid-knuth` was working in `src/aegis/tui/`", so the
coordination signal survives. Only the wall comes down. A write into a dormant
claim takes the notice-and-shadow path of policy line 3, and the notice lands
in the dormant agent's inbox.

**The notification is the liveness probe.** If that agent is actually still
alive, the inbox message wakes it and it can re-claim `exclusive` and object.
If it is genuinely done, nothing was harmed and no operator had to adjudicate.
This is why the design needs **no `force=true` verb, no break-in tool, and no
timeout that someone has to tune** — recovery falls out of machinery that
already exists.

### Restart and dropped hosts need no code

`registry.start()` replays the JSONL and prunes against restored panes, so
today every claim survives a restart — including ghost walls from sessions that
will never resume. Under the dormant rule a restored-but-unresumed session has
no recent turn and nothing pending, so it degrades on contact. The same
self-heal covers a dropped SSH host connection, where the remote session is
dead but the local pane persists.

That the restart case requires no special handling is a good sign the rule is
placed correctly.

## The `aegis_close` regression

`aegis_close` refuses to close an agent holding file claims. The count at
`mcp/server.py:1155` is:

```python
claims = len([c for c in (locks.active() or [])
              if getattr(c, "handle", None) == handle])
```

Under auto-claim, **every agent that has ever edited a file holds claims**, so
close would start refusing almost universally and reaping one's own workers
would break.

Fix: filter to explicit claims (`and not c.auto`), and have `bridge.close()`
release the target's auto-claims silently. Written down here because this is
otherwise discovered three weeks later as "why can't I close anything".

## Notifications and shadow copies

Three messages, all on existing plumbing:

1. **To the writer**, in-context on its next turn — "you wrote into
   `lucid-knuth`'s shared claim on `src/aegis/tui/`."
2. **To the holder**, via the inbox — the most valuable of the three. Today a
   peer writing under your feet is discovered at `git diff`, hours later. A
   one-line inbox notification closes that loop, and doubles as the liveness
   probe above.
3. **To Alex**, in the TUI.

On any write into a **foreign** claim, the PEP snapshots the prior file content
to `.aegis/state/locks/shadow/` **before** the write, and puts the restore path
in the notice. This makes the undo real rather than aspirational: an agent has
no ctrl+z and no copy of what it overwrote. The path is rare by construction so
the cost is nil, and it is more reliable than `git checkout --` because the file
is usually already dirty mid-work.

Shadow copies are pruned on session end alongside claims.

## UI

### The trap that shapes all three surfaces

With auto-claim, a session that has edited thirty files holds thirty claims. A
surface that lists them is a scrolling wall of paths the agent already knows it
touched, at 26 columns. So the ranking is **by what demands action**, not by
recency or path:

1. **Contested** — my claims a live peer overlaps, and foreign claims blocking
   me. The only rows worth a full line.
2. **Explicit** — claims the agent deliberately asked for, with an intent
   marker. Few, and they are statements of intent.
3. **Auto** — rolled up to one line, folded to the common prefix:
   `auto · src/aegis/ · 12 files`.

A contested row leads with the **peer handle**, not the path: the handle is
what you act on (`handoff lucid-knuth`), the path is context. When the row
narrows, elide the path and keep the handle. This is the opposite of the usual
instinct and it is what keeps the row actionable at the narrowest tier.

### Sidebar (`F3`)

Follow the plan module's split exactly: a free-standing pure renderer
`aegis/locks/render.py::render_claims_dock(snapshot, palette, width)` with its
own contract and its own test, and a `sidebar._claims` section composing it —
trimming framing at the composition site the way `_plan()` trims
`render_plan_dock`'s header, rather than changing the renderer.

Slot into `SECTIONS` (`tui/sidebar.py:174`) after `_plan`:

```
_session, _context, _plan, _claims, _queues, _monitors, _system
```

PLAN is what I intend, CLAIMS is what I am touching; QUEUES and MONITORS are
outbound async work and belong below.

Four constraints this repo has already paid for, all of which bite here:

- The `width` passed in **is** the content box. Do not subtract
  `SIDEBAR_PAD_X` again — `SIDEBAR_MIN`/`SIDEBAR_MAX` already carry it.
- Measure in **cells, not `len()`**. A 🔒 is one character and two columns and
  will drift the row's right-hand column. Either space-separate it the way the
  plan circles are, or use a single-width marker.
- `fit_rows` answers "no tier fits" by **omitting the segment entirely**, so
  every row needs a genuinely short narrowest form — otherwise a conflict
  silently vanishes at 80 columns, which is the worst possible failure for this
  feature.
- A session with no claims hides the section via the **`-empty` class**, never
  by setting `display` imperatively (the `PlanStrip` lesson).

Refresh comes off a `ClaimRegistry` observer callback, not polling; claims now
change on every write, so the PEP is the natural emitter.

### Status bar

`StatusBar`'s ladder is ranked by "does this change, and does it demand
action", which gives claims an unusually clean answer: **the same segment
deserves two priorities.**

```python
P_CLAIMS_ALERT = 55   # between P_STATE (60) and P_LOOP (50)
P_CLAIMS_QUIET = 28   # just below P_METRICS (30)
```

Quiet claims are telemetry and should degrade away early; a contested claim
demands action and should survive to the narrowest terminal. Segments are
constructed fresh on every compose (`tui/widgets.py:454`), so the priority is
simply computed at construction — no second segment, no registration dance.

Tiers: `3 claims · 1 contested` → `3c 1!` → `1!`.

**Trap:** `fit.plain_width()` measures with `len()`, not `cell_len()` — its
docstring asserts that the glyphs the bar uses are single-width. Status-bar
claim glyphs must therefore stay single-width, or `plain_width` has to move to
`cell_len` first. (`truncate_cells` already uses `cell_len`; the two are
inconsistent today and this feature is where that would surface.)

### `/claims`

The sidebar and status bar are inherently per-pane. There is currently no way
to answer "who is working where across all eight tabs" without asking an agent
to call the MCP tool. A `/claims` slash command closes that.

Registered in `commands/builtins/core.py` alongside `/sessions` — it is a
roster view, same family. Shape follows `_sessions` exactly:

```python
async def _claims(ctx: CommandContext, args) -> CommandResult:
    ...
SlashCommand("claims", "show the file-claims board", "/claims", _claims)
```

Output is the whole board grouped by holder, contested rows first, with the
same auto-rollup as the sidebar. Reads through the existing `_LocksBridge`
(`ctx.bridge`), so no new data path.

## Data model changes

- `Claim` gains `auto: bool = False`.
- `Claim` gains nothing else — `since` plus the holder's session activity is
  enough to compute dormancy; do not denormalize a heartbeat onto the claim.
- `PersistedClaimLog` gains record types for `degraded` and `violation`
  alongside the existing `claimed` / `released` / `reaped` / `renamed`.
- `ClaimRegistry` gains a `check(handle, path, op) -> Decision` method — the
  PDP entry point every PEP calls — and an observer callback for UI refresh.
- `live_handles` callables in `core/manager.py:460` and `tui/app.py:428` are
  joined by a `session_activity` callable supplying the dormancy inputs.

## Config

One kill-switch, because a mandatory mechanism that cannot be turned off is a
mechanism that will eventually strand someone:

```yaml
locks:
  enforce: true          # false → today's advisory behavior
  dormant_after: 20m
```

`enforce: false` must disable the PEPs but **keep auto-claim**, so the board
stays accurate even when the walls are down.

## Testing

- `ClaimRegistry.check()` is pure and gets a table-driven test over the five
  policy lines × (auto | explicit) × (shared | exclusive) × (live | dormant |
  gone).
- `render_claims_dock` gets its own contract test at several widths, including
  the 26-column floor, asserting that a contested row **survives** — this is
  the `fit_rows` omission failure and it must be pinned.
- The Bash heuristic gets a corpus of real commands with expected verdicts,
  including unparseable ones that must pass.
- The `aegis_close` fix gets a regression test: an agent with only auto-claims
  closes cleanly.
- Mutation check per house rule: break the PEP on purpose and confirm the
  enforcement tests go red. A gate that cannot fail is worth less than none.

## Deferred

- Kernel-level enforcement (FUSE / uid+ACL) — see threat model.
- Cross-host claim negotiation beyond `Claim.host` scoping.
- Operator-facing violation dashboard; the JSONL log is enough to start.
- Auto-claim TTL / compaction. Revisit if a long session's board grows
  unwieldy in practice.
