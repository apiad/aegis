# Aegis Claims Registry — Design

**Status:** approved (brainstormed 2026-07-10)
**Primitive:** `aegis_claim` / `aegis_release` / `aegis_claims` — inter-agent
file-claims registry with conflict surfacing.

## Why

The workspace has an ad-hoc lock today: `bin/ws-lock` over a SQLite
`LockRegistry` at `.claude/state/locks.db`, keyed by session id / PID,
per-host, advisory, released explicitly (with a PID-liveness `gc`). It works
for "don't clobber each other" but it is external to aegis: agents shell out
to a CLI, conflicts are exit-code 2, and there is no channel from "you
conflict" to "let's coordinate."

Aegis already knows every agent, their liveness, and gives them a tagged
inbox to talk to each other. That makes a **native** claims primitive strictly
better for aegis-hosted agents: an agent claims files, learns *who else is
working there and how*, and — because the overlapping party is a known peer
with an inbox — can collaborate, ask, or back off instead of just failing.

This reframes the primitive away from a hard mutex toward a **presence /
intent registry with conflict surfacing**, plus one genuine hard-gate mode
(`exclusive`) for "keep out while I refactor."

## Scope

- **Replacement:** *new surface, coexist.* Build fresh on its own store under
  `.aegis/state/locks/`; leave `bin/ws-lock`, the `using-workspace-locks`
  skill, and the CLAUDE.md guidance untouched. Retiring / migrating ws-lock is
  a deliberate follow-up once this has proven itself.
- **Per-host, v1.** Scoped to one `aegis serve` instance and its agents.
  Cross-host locking over the remotes/mesh plane is a later concern.
- **Aegis agents only.** This is an MCP tool; non-aegis sessions keep using
  `ws-lock`. That coverage gap is accepted for v1 (hence "coexist").

## Model

An agent **claims** a set of paths with an **intent**:

- `shared` (default) — "I'm working here, FYI." Multiple agents may hold
  overlapping `shared` claims simultaneously.
- `exclusive` — "keep out." A real gate.

**Grant rule** on a new claim `C` against the set of active claims:

- Compute overlaps: any active claim whose path-set intersects `C`'s.
- `C.intent == shared`:
  - Overlaps only with other `shared` claims → **granted**; overlaps returned.
  - Overlaps with any `exclusive` claim → **denied**; conflicting holders
    returned.
- `C.intent == exclusive`:
  - Any overlap at all (shared *or* exclusive) → **denied**; conflicting
    holders returned.
  - No overlap → **granted**.

So `shared∩shared` coexists (surfaced), and anything touching an `exclusive`
region is refused. Denial hands back the holders so the newcomer negotiates
over the inbox rather than barging in.

### Path atom & overlap math

A claim's `paths` is a list that may contain:

- **prefixes** — a trailing `/` marks a subtree (`src/aegis/tui/`).
- **concrete files** — `src/aegis/mcp/server.py`.
- **globs** — `src/aegis/tui/*.py`; **resolved to concrete paths against the
  current tree at claim time** and stored as the resolved set.

After resolution a claim is `{prefixes: set[str], files: set[str]}`. Two claims
overlap iff:

- their `files` sets intersect, **or**
- a file in one falls under a prefix in the other, **or**
- one prefix is a prefix of the other.

This is cheap prefix/set math — the "who else is here?" answer is instant and
exact. We deliberately do **not** attempt glob-vs-glob intersection (undecidable
without enumerating the FS); globs are always resolved to concrete paths first.

## Surface

```python
aegis_claim(paths: list[str], from_handle: str,
            intent: str = "shared", desc: str = "")
    -> {"claim_id": str, "granted": bool,
        "overlaps": [{"handle", "paths", "intent", "desc"}]}

aegis_release(claim_id: str, from_handle: str) -> {"released": bool}

aegis_claims() -> [{"claim_id", "handle", "paths", "intent", "desc", "since"}]
```

- `aegis_claim` — always returns the `overlaps` it found, whether or not the
  claim was `granted`. On a `granted=false` (exclusive conflict) the claim is
  **not** recorded; `overlaps` carries whom to talk to.
- `aegis_release` — idempotent; releasing someone else's claim is a no-op.
- `aegis_claims` — the board: every active claim. Backs an agent's "what's
  everyone touching?" question and, later, a TUI dashboard.

## Lifecycle

- A claim is held **across turns** until explicit `aegis_release` **or** the
  holder's session closes.
- **Auto-reap on session close** — the substrate already knows session
  liveness, so a dead agent's claims are released automatically. This is
  strictly better than ws-lock's PID-liveness `gc` hack.
- Persistence follows the `queue/` and `groups/` pattern: append-only JSONL
  lifecycle log at `.aegis/state/locks/claims.jsonl` (`claimed` / `released` /
  `reaped` records), in-memory registry as the source of truth, boot replay
  rebuilds the live set and reaps any claim whose holder is no longer a live
  session. Torn-trailing-line tolerant.

## Docstrings (teach the whole model)

Per explicit request, the tool docstrings carry the operating manual, because
that is how an agent learns the semantics. `aegis_claim`'s docstring must
cover:

> Register that you are working on a set of files, and find out who else is.
> `intent="shared"` (default) means "I'm working here, FYI" — other agents can
> hold overlapping shared claims too; you'll just see each other. `intent=
> "exclusive"` means "keep out" and is refused if it overlaps anyone.
>
> The response always includes `overlaps`: the other agents touching the same
> paths, with their handles and intent. **When you overlap someone, the right
> move is to coordinate, not to barge in** — `aegis_handoff` the holder to ask
> what they're doing, agree who owns what, wait for them to `aegis_release`, or
> narrow your own claim. If your `exclusive` claim was denied (`granted:false`),
> it was *not* recorded — resolve the conflict with the listed holders first,
> then re-claim.
>
> Release with `aegis_release(claim_id)` when done (claims also auto-release
> when your session ends). See the whole board with `aegis_claims()`.

## Architecture

New package `src/aegis/locks/`, mirroring `queue/` and `groups/`:

- `models.py` — `Claim` (`claim_id` via `new_ulid`, `handle`, `prefixes`,
  `files`, `intent`, `desc`, `since`), overlap predicate helpers.
- `resolver.py` — `resolve_paths(paths, root) -> (prefixes, files)`: splits
  prefixes/files, expands globs against the tree.
- `registry.py` — `ClaimRegistry`: in-memory map, `claim()` applying the grant
  rule, `release()`, `active()`, `reap(handle)`; emits persistence events.
- `persistence.py` — JSONL writethrough + boot replay (same shape as
  `groups/persistence.py`).
- `bridge.py` — a `_LocksBridge` surface the MCP server consumes. Reap wiring:
  at spawn time each `AgentSession` gets a close observer
  (`session.add_close_observer`, session.py:128 — the same per-session hook
  `GroupWiring` uses for its event observers) that calls `registry.reap(handle)`.

MCP tools in `server.py`: `aegis_claim`, `aegis_release`, `aegis_claims`.
Registered like the group/queue tools; `from_handle` threaded through.

## Non-goals (v1)

- No cross-host / mesh locking.
- No auto-notification of holders — the claiming agent decides whom to ping.
- No `ws-lock` retirement or shared store (coexist; retire later).
- No glob-vs-glob intersection.
- No claims dashboard (`Ctrl+L`) — deferred to a follow-up; `aegis_claims()`
  is the v1 board.

## Testing strategy

Hermetic, mirroring the groups/queue tests:

- `resolve_paths` — prefixes vs files vs globs expand correctly against a
  temp tree.
- Overlap predicate — file∩file, file-under-prefix, prefix-under-prefix,
  disjoint.
- Grant rule — shared∩shared granted+surfaced; shared over exclusive denied;
  exclusive over anything denied; exclusive over empty granted.
- Lifecycle — release idempotent; releasing another's claim no-ops; boot replay
  rebuilds live set; `reap(handle)` on session close drops that agent's claims.
- MCP round-trip — `aegis_claim` / `aegis_release` / `aegis_claims` shapes;
  `granted=false` does not record the claim.
- One live-ish integration: two `FakeSession`s, A claims exclusive, B's
  overlapping claim is denied with A in `overlaps`, A closes → B re-claims and
  is granted.

## References

- `bin/ws-lock`, `.claude/scripts/claude_locks.py` — the mechanism being
  superseded (for aegis agents).
- `src/aegis/groups/` — the package shape this mirrors (`models`, `registry`,
  `persistence`, `bridge`, MCP wiring).
- `src/aegis/queue/` — JSONL lifecycle-log + boot-replay pattern.
- `src/aegis/core/session.py` — `add_close_observer` (the per-session reap hook).
