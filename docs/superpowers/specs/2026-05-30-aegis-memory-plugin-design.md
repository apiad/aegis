# Aegis memory plugin — design

**Date:** 2026-05-30
**Status:** Design — pending plan
**Goal:** A canonical second aegis plugin (after `skill-system`) that gives every session in a project a persistent, self-curating memory. Hermes-inspired in shape and naming; built end-to-end on the v1 plugin substrate primitives (`@hook`, `@tool`, `@workflow`). Acts as the showcase that the substrate is general enough to host non-trivial second plugins.

## Motivation

The plugin substrate (v0.15.0) shipped with one canonical plugin, `skill-system`. To prove the substrate generalizes, we want a second plugin that exercises every primitive shape — hooks (both `session_start` and `pre_turn`), tools (multiple), and a long-running scheduled workflow — and that produces a result legible enough to put in the README ("the agent had an insight while you were asleep").

Hermes Agent (Nous Research, Feb 2026) is a good template: it splits agent memory across markdown files (`SOUL.md` / `MEMORY.md` / `USER.md`), exposes a `memory` tool for add/replace/remove, and runs a "reflective phase" that periodically synthesizes recent sessions back into memory. We lift the conceptual layers and naming for legibility but ship a smaller v1 scoped to what the existing aegis substrates make cheap.

## Scope (v1)

**In:**

- Markdown memory files under `.aegis/memory/` (per-project).
- Five MCP `@tool`s for read/write/search.
- Two `@hook`s (`session_start`, `pre_turn`) that load and inject memory.
- A `@workflow` (`dream`) that periodically consolidates and synthesizes.
- Plugin install/uninstall via the existing `InstallContext` substrate.

**Out (deferred to v2):**

- SQLite + FTS5 message log (Hermes `state.db`). We use the existing `.aegis/state/sessions/*.jsonl` transcripts instead.
- Auto-generation of new skills from successful tool sequences.
- Embeddings-based search.
- Cross-project memory or multi-user `USER.md` overlays.
- Self-rewriting `SOUL.md` (dream pass never touches the persona layer).

## Filesystem layout

Everything lives under the project root, alongside the other `.aegis/` substrate directories:

```
.aegis/memory/
  SOUL.md                                # user-edited persona; hook reads it
  USER.md                                # user-edited identity; hook reads it
  MEMORY.md                              # one-line index of every entry
  entries/
    user_name.md                         # frontmatter: type, name, description, …
    feedback_no_load_bearing.md
    fact_dream_runs_at_3am.md
    reference_grafana_dashboard.md
  dreams/
    dream-2026-05-30.md                  # narrative dream log per cycle
    dream-2026-05-31.md
  .lock                                  # held by the dream workflow during consolidation
```

### Who writes what

| File / dir | User edits | Agent writes (via tools) | Dream workflow writes |
|---|---|---|---|
| `SOUL.md` | yes | no | **no** |
| `USER.md` | yes | no | no |
| `MEMORY.md` | no (regenerated) | yes (index line on add/replace/remove) | yes (consolidation) |
| `entries/*.md` | optional | yes | yes (consolidation + synthesis) |
| `dreams/*.md` | no (read-only artifact) | no | yes (append one per cycle) |

The `dream-*.md` files are write-only artifacts for the human user. The agent never reads them back. This keeps them out of the per-turn injection budget and makes them feel like *artifacts*, not state.

## Memory entries

Each entry is a markdown file with YAML frontmatter:

```markdown
---
type:        feedback
name:        no-load-bearing
description: User hates the phrase "load-bearing"; substitute or omit
created:     2026-05-30T19:51:00Z
updated:     2026-05-30T19:51:00Z
---

User dislikes the phrase "load-bearing" in agent output. Avoid it in
all written work, including drafts and thesis statements. Substitute
"essential", "central", "decisive", or drop the qualifier entirely.
```

### Four types

| Type | Purpose |
|---|---|
| `user` | Identity, role, goals, knowledge of the human |
| `feedback` | Corrections + preferences ("don't do X", "always do Y") |
| `fact` | Durable project/code facts the agent had to discover |
| `reference` | Pointers to external systems (URLs, dashboards, issue trackers) |

"Project" is not a separate type because the entire memory is already project-scoped.

### Slugs

The on-disk filename is `<type>_<name>.md`, with `<name>` sanitized to kebab-case. The slug is `<type>_<name>` (the filename without `.md`). Slugs are stable for the life of the entry — `memory_replace` cannot rename; rename = `memory_remove` + `memory_add`.

### `MEMORY.md` index format

```markdown
# Memory index

## Index

- [user-name](entries/user_name.md) — User goes by Alex
- [no-load-bearing](entries/feedback_no_load_bearing.md) — User hates "load-bearing"; substitute or omit
- [dream-runs-at-3am](entries/fact_dream_runs_at_3am.md) — Default dream cron is 03:00 local
- [grafana-dashboard](entries/reference_grafana_dashboard.md) — grafana.internal/d/aegis observability board
```

One line per entry; description = the entry's frontmatter `description` field. The index is regenerated by every `memory_add` / `memory_replace` / `memory_remove` call. On corruption (frontmatter unreadable, file deleted out-of-band), it is rebuilt from `entries/` on the next `memory_add`.

## Hooks

### Two hooks: `session_start` (observer) and `pre_turn` (mutator)

The v1 hook substrate makes `pre_turn` the only mutator event; `session_start`, `post_turn`, and `session_end` are observer-only. The plugin therefore does **all injection in `pre_turn`**, with a turn-counter branch: turn 0 injects the full bundle (SOUL + USER + MEMORY index + judgment primer); turns ≥ 1 inject only the relevance-matched teasers.

A separate `@hook("session_start")` is used purely for observability — it appends one line to the plugin's own JSONL log noting which session opened, what memory files were present, and the size of the MEMORY index. It does not affect agent context.

### `@hook("pre_turn")` — turn 0 branch (fires once)

Builds and returns a `PreTurnResult(prepend_system=…)` whose body is, in order:

1. `SOUL.md` verbatim. Skipped if absent.
2. `USER.md` verbatim. Skipped if absent.
3. The `MEMORY.md` index (names + descriptions, **no bodies**).
4. The judgment primer (see below).

**Cap: 4,000 tokens total.** If exceeded, the MEMORY index is truncated to most-recently-updated entries (per file mtime); a trailing line says `… N more entries; use memory_search to find specific ones`. The judgment primer is fixed-size (~400 tokens) and not subject to truncation — if SOUL + USER alone exceed the cap, an error is logged and only the primer is injected.

The primer is injected **only on turn 0** — once the agent has it, the harness's own caching keeps it warm. Re-injecting every turn would be wasteful.

### `@hook("pre_turn")` — turn ≥ 1 branch

Two-pass cheap heuristic, no embeddings:

1. **Keyword match.** Tokenize the user message, lowercase, drop stopwords. For each entry, `score = (# message tokens in name + description) × 2 + (# in body) × 1`. Keep entries with `score ≥ 2`.
2. **Recency boost.** `+1` to entries written or updated in the last 24h.

Take the top **5** entries by score and inject **name + description only** (no body) under a `## Possibly relevant memory` heading. To read a body, the agent must call `memory_read(slug)` or `memory_search(query)`.

**Cap: 1,000 words (≈1,300 tokens) per turn.** Entries are added in score order until the cap; never truncate mid-entry. If nothing scores ≥ 2, inject nothing.

### The judgment primer (text the hook injects)

```
# Memory

You have a persistent memory at .aegis/memory/. The MEMORY.md index above
lists everything you know. Use memory_search(query) to find an entry's
body, or memory_read(slug) when you already know the slug.

Write a memory when:
- the user corrects you ("don't", "stop X") → save as `feedback`
- the user reveals a preference, role, or constraint → `user`
- you discover a non-obvious fact about the project or tooling → `fact`
- the user names an external system you'll need again → `reference`

Skip trivial / easily-rediscovered things. When unsure, save — the
dream pass will consolidate later.

Tools:
- memory_search(query)         — find entries by keyword
- memory_read(slug)            — fetch one entry's body
- memory_add(type, name, …)    — save a new memory
- memory_replace(slug, …)      — update an existing one
- memory_remove(slug)          — delete (use sparingly outside dream pass)
```

Roughly 400 tokens. Injected once on turn 0 only; not re-injected per turn.

## Tools

All five registered via the v1 `@tool` decorator and exposed through FastMCP. Each timeout-wrapped + JSONL-logged by the existing tool runner. Default per-tool timeout: 5s (filesystem-only).

### `memory_add(type, name, description, content) → {slug, path}`

Writes a new entry to `.aegis/memory/entries/<type>_<name>.md`. Type must be one of `user | feedback | fact | reference`. Name sanitized to kebab-case. Frontmatter (`type`, `name`, `description`, `created`, `updated`) generated automatically. Appends an index line to `MEMORY.md`.

Fails loud (clear error to agent) if a slug already exists — agent should `memory_replace` instead.

### `memory_replace(slug, *, description=None, content=None) → {slug, path}`

Updates an existing entry. `name` and `type` are immutable — keeps the slug stable for citations across sessions. Updates `updated:` timestamp. If `description` changes, touches the `MEMORY.md` index line.

### `memory_remove(slug) → {slug, removed: true}`

Deletes the entry file and its index line. No soft-delete, no archive. The dream pass uses this freely during consolidation.

### `memory_search(query, *, limit=10) → [{slug, name, description, score, snippet}]`

Plain markdown search — no FTS5, no embeddings. Scoring matches the `pre_turn` heuristic. Returns top `limit` entries with a 200-char snippet around the best body match.

### `memory_read(slug) → {slug, name, type, description, content}`

Direct fetch of a single entry's full body. Cheaper than `memory_search` when the slug is known (typically from the `pre_turn` teaser list).

## The dream workflow

A `@workflow`-decorated function in the plugin, registered like any other aegis workflow. Three sequential stages; stage 1 fans out subagents in parallel via the existing `WorkflowEngine.delegate(queue=…)` primitive.

### Config

Read from `.aegis.yaml` under a `memory:` block the plugin install adds:

```yaml
memory:
  lookback_days:       7
  max_session_files:   50
  dreamer_agent:       dreamer
  dream_cron:          "0 3 * * *"   # present only if user opted in at install
```

Install also adds a stub `dreamer` agent profile to `.aegis.yaml` (Claude Haiku, low effort, read-write). User can swap.

### Stage 1 — Read transcripts (parallel)

Glob `.aegis/state/sessions/*.jsonl`, filter to files modified within `lookback_days`, cap at `max_session_files`. For each, dispatch a `dreamer`-profile subagent.

Subagent prompt: "Here is one aegis session transcript. Summarize what happened, propose memory entries the agent should have saved but didn't, and note observations — surprising patterns, contradictions, repeated stumbles." Returns structured JSON (schema-validated; bad output triggers a retry):

```json
{
  "session_handle": "lucid-knuth",
  "summary": "…",
  "proposed_entries": [
    {"type": "feedback", "name": "…", "description": "…", "content": "…", "rationale": "…"}
  ],
  "observations": [
    "Agent re-discovered the same Docker quirk three times.",
    "User consistently switches to Spanish when discussing the Enciclopedia repo."
  ]
}
```

### Stage 2 — Consolidate (sequential, one agent)

A single `dreamer` subagent. Receives all current `entries/*.md` (full bodies, paginated if large) + every `proposed_entries` list from stage 1. Emits an action plan:

```json
{
  "actions": [
    {"action": "add",     "type": "fact", "name": "…", "description": "…", "content": "…"},
    {"action": "replace", "slug": "feedback_phrasing", "content": "…merged…"},
    {"action": "remove",  "slug": "fact_old_stale_thing"}
  ],
  "rationale": "…short justification log…"
}
```

The workflow applies each action by calling the memory tools (`memory_add` / `memory_replace` / `memory_remove`). The `rationale` is carried forward to stage 3. The workflow holds `.aegis/memory/.lock` for the duration of stage 2 so concurrent agent writes don't race.

### Stage 3 — Synthesize the dream log (sequential, one agent)

A single `dreamer` subagent. Receives all `observations` from stage 1 + stage 2's `rationale` + the now-updated MEMORY index. Asked to write a **narrative dream log** — prose, not JSON. ~500–1000 words. Style: introspective, first-person from the agent's perspective.

The workflow writes the prose to `.aegis/memory/dreams/dream-YYYY-MM-DD.md` with a frontmatter block listing:

- `actions:` — the count and slug list of stage-2 mutations
- `sessions_read:` — the session handles dreamed over
- `cost_usd:` — total dreamer-token cost
- `lookback_days:` — the window used

During stage 3 the agent may also drop 0–N additional high-level `fact` entries (the "I noticed a pattern" kind) via `memory_add`. These are observations that aren't tied to any single session.

### Triggering

The plugin ships only the `@workflow`. At install, the user is asked once whether to schedule it daily at 03:00. If yes, install **calls `aegis.scheduler.push.write_atomic(...)`** to write an overlay schedule file at `.aegis/schedules/memory-dream.yaml`. Going through `write_atomic` (rather than appending to `.aegis.yaml` inline) is what gives the schedule a real existence — it's the same path the `aegis schedule` CLI uses, it validates the spec against the workflow registry before writing, and a running `aegis serve` picks the new file up via the `ReloadWatcher` within the debounce window. A bare append to `.aegis.yaml` would persist but never *register*.

Note that **cron only fires while a long-running aegis process is up** (`aegis` or `aegis serve`) — the scheduler lives in that process. If the user runs the plugin in a project where they only invoke aegis ad-hoc, the cron entry is dormant until next start. The install summary message surfaces this explicitly ("dream scheduled at 03:00 — fires whenever `aegis serve` is running"). Either way, the workflow remains runnable on demand via `aegis workflow run dream`.

### Cost shape

Per dream: O(N) where N = session files in window. Stage-1 subagents each read ~1 session (~10K tokens typical); stages 2 + 3 read O(current MEMORY size + stage 1 output). With Haiku as the dreamer and 30 sessions × 5K tokens, one dream is ≈ $0.10–$0.30.

## Plugin install and uninstall

### `plugin.toml`

```toml
[plugin]
name           = "memory-system"
version        = "0.1.0"
description    = "Hermes-inspired persistent memory with periodic dreaming."
requires_aegis = ">=0.15"

[default_config]
lookback_days     = 7
max_session_files = 50
dreamer_agent     = "dreamer"
```

### `_install.py::install(ctx)`

1. Create `.aegis/memory/{entries,dreams}` (idempotent).
2. Write stub `SOUL.md`, `USER.md`, `MEMORY.md` if absent (10/5/3-line templates).
3. Add a `dreamer` agent profile to `.aegis.yaml` if not present:
   ```yaml
   dreamer:
     provider:   claude
     model:      haiku
     effort:     low
     permission: read-write
   ```
4. Add a `memory:` block with `default_config` values.
5. `ctx.confirm("Schedule the dream pass daily at 3am? [Y/n]", default=True)`. If yes, call `aegis.scheduler.push.write_atomic(state_root=ctx.aegis_dir, name="memory-dream", spec=…)` with the spec:
   ```yaml
   workflow:  dream
   cron:      "0 3 * * *"
   lifecycle: forever
   ```
   This writes the validated overlay to `.aegis/schedules/memory-dream.yaml`. Any running `aegis serve` picks it up via `ReloadWatcher`; if no aegis process is running, the schedule loads at next start.
6. Print a one-line summary of created paths + cron status. If the cron was installed, mention explicitly that it fires only while `aegis serve` (or `aegis`) is running.

### `_uninstall.py::uninstall(ctx)`

1. Strip from `.aegis.yaml`: the `memory:` block and the `dreamer` agent (only if unused by other queues/schedules). Delete the overlay file `.aegis/schedules/memory-dream.yaml` (the scheduler's `ReloadWatcher` removes it from the live table on next reload).
2. `ctx.confirm("Also delete .aegis/memory/ and all stored memories and dream logs? [y/N]", default=False)`. Default **N** — uninstall must not silently lose accreted memory.
3. Print confirmation.

All YAML edits go through `aegis.config.edit` (ruamel, comment-preserving, atomic rename).

### Registry discoverability

Installable from any project via:

```
aegis plugin install memory-system --from gh:apiad/aegis#plugins/memory-system
```

The aegis repo's own `plugins/` folder is the default registry served at `gh:apiad/aegis#plugins/`.

## Error handling

**Fail-soft at session level.** A misbehaving memory plugin must never break a turn:

- Missing `SOUL.md` / `USER.md` / `MEMORY.md` → hooks skip silently, log to JSONL.
- Corrupt entry frontmatter → entry excluded from injection and search, never crashes the hook. Tool runner reports the bad file.
- Tool exception → the v1 tool runner already wraps with `try/except` + structured error to the agent. Agent decides whether to retry.
- Corrupt `MEMORY.md` index → rebuilt from `entries/` on next `memory_add`.

**Atomic writes.** All write operations use tempfile-+-rename. No partial files on crash.

**Dream workflow isolation.** Stage-1 subagent failures are caught at the workflow-engine level; one bad transcript drops from that day's dream, others still run. Stage-2 and stage-3 failures are loud — non-zero exit, schedule logs the failure, no partial dream file written (atomic rename only at the very end).

**Concurrency.** Dream stage 2 holds `.aegis/memory/.lock` for the consolidation window. Concurrent agent `memory_*` calls block briefly (seconds). Stages 1 and 3 do not need the lock.

## Testing

Mirroring the `skill-system` plugin's test layout under `repos/aegis/tests/`:

- `test_memory_tools.py` — hermetic, `tmp_path`, all five tools, round-trip + frontmatter + index consistency.
- `test_memory_hooks.py` — hermetic, synthetic entries on disk; assert `session_start` injects SOUL+USER+index, `pre_turn` picks the right top-5 + respects the 1,000-word cap.
- `test_memory_dream.py` — hermetic, mock `WorkflowEngine.delegate` with scripted JSON; run the workflow against a fake session JSONL dir; assert memory mutations, dream log written, frontmatter correct.
- `test_memory_install.py` — hermetic, `tmp_path` project; assert tree, stub files, YAML edits. Round-trip with uninstall; assert YAML stripped, memory dir preserved by default.
- `test_memory_system_live.py` — `@pytest.mark.live`, skips if `claude` absent. Install plugin → start a session → user says "remember that I prefer Spanish for the Enciclopedia repo" → assert an entry was written.

The **dream workflow has no live test in v1** — too expensive, too flaky. Hermetic stage-1/2/3 with mocked LLM responses is sufficient. Manual smoke via `aegis workflow run dream` after install.

## Open questions deferred to plan

- Exact stopword list for the `pre_turn` keyword matcher (a 50-word English list is fine for v1).
- Whether the install prompt should also offer a non-3am default (e.g. `--cron "0 4 * * *"` flag passthrough). Probably not in v1; user can edit `.aegis.yaml` after install.
- Whether `memory_search` snippets should be highlight-marked. Not in v1.

## Acceptance criteria

A v1 ship is accepted when:

1. `aegis plugin install memory-system --from plugins/memory-system` succeeds against a clean project, creates the directory tree, edits `.aegis.yaml`, and (if user agrees) installs the cron schedule.
2. A claude session in that project loads SOUL + USER + index at start, sees relevant teasers per turn, and can call all five tools to round-trip a memory entry.
3. `aegis workflow run dream` against a populated `.aegis/state/sessions/` directory writes a `dream-YYYY-MM-DD.md` and applies at least one consolidation action.
4. `aegis plugin uninstall memory-system` strips the YAML config + schedule but preserves the memory directory by default.
5. All hermetic tests pass under `uv run pytest -q -m "not live"`. The live test passes when `claude` is on PATH.
