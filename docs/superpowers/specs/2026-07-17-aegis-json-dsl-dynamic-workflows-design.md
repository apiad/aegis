# Aegis JSON DSL — Dynamic Workflows (Track 2)

**Status:** design
**Date:** 2026-07-17
**Supersedes framing of:** `docs/superpowers/specs/2026-05-22-workflow-catalog-design.md` (see § Relationship to Track 1)

## Summary

Aegis workflows split into two tracks that differ in *form* and in the safety
mechanism that licenses an **agent** to author them:

- **Track 1 — `@workflow` (durable Python).** Arbitrary Python driving the
  substrate. Stored in the catalog, reusable. Full imperative power (loops,
  state, computation). Licensed by a **human approval gate**: an
  operator-invoked run (`/workflow`) is implicitly approved; an agent-invoked
  run prompts the operator, who reviews the **script**.
- **Track 2 — JSON DSL (dynamic workflows).** A validated JSON document
  describing a fan-out/pipeline orchestration. Ephemeral by default. Bounded
  control flow (no arbitrary code). Licensed by **schema validation**: a
  malformed spec is rejected at the tool boundary before it can run; a valid
  spec is safe by construction.

This document specifies Track 2. It is aegis's answer to Claude Code's
"dynamic workflows" (June 2026): the same describe-a-task-and-fan-out
ergonomics, but the units orchestrated are **real aegis agents across
harnesses** (spawn any profile, hand to a live session, delegate to a queue),
the run is **durable across process restarts and hosts**, and it can **pause
for the operator** mid-run — three things a single in-harness feature cannot do
by construction.

The tracks partition cleanly by control-flow shape: **static/bounded → data
(Track 2); iterative-with-state/computational → code (Track 1)**. They compose:
a Track-1 durable conductor may launch Track-2 fan-outs.

## Motivation

When this subsystem's original catalog was designed (2026-05-22), no coding-agent
harness could orchestrate multi-agent patterns, so aegis workflows filled that
gap as human-authored Python. That premise no longer holds. As of mid-2026,
Claude Code ships **Dynamic Workflows** (model writes a JS orchestration script,
fans out to hundreds of subagents, adversarially verifies, runs in the
background), Codex ships subagents + Skills + Automations, and Gemini CLI ships
parallel subagents. Intra-harness, single-provider orchestration is now
vendor-owned and better-integrated than a static Python catalog.

What no single harness can do — because each is one process talking to one
provider — is orchestrate **across** harnesses, **across** sessions and hosts
(durably), and **with a human in the loop mid-run**. That is aegis's structural
moat, and Track 2 is the surface that exposes it to *agents*, so an agent can
author, validate, and launch such an orchestration on the fly the way it can a
Claude dynamic workflow — but over aegis's heterogeneous, durable substrate.

## Design principles

1. **Code for the engine, data for the instance.** The interpreter is trusted
   human-written Python; the workflow an agent produces is validated data. This
   is the AWS Step Functions lineage (state machine as validated JSON,
   interpreted by a trusted runtime), not the Airflow lineage (DAGs as code).
2. **Select, never compute.** Every place data is referenced — selectors,
   templates, `map.over`, judge inputs — *navigates* structure; it never
   calculates. All computation (dedup, rank, merge, transform) is delegated to
   an `agent` node. This is the same ruthless line drawn twice, and it is what
   keeps the DSL from growing into a programming language inside JSON.
3. **Bounded control flow.** Static shapes plus hard-bounded `loop`/`if`. Every
   loop carries a mandatory integer `max_rounds`. Anything needing unbounded
   iteration, accumulators, or arbitrary state belongs in Track 1.
4. **Safe by construction, gated by cost.** A validated spec cannot execute
   arbitrary code, so it needs no *safety* gate — only a *cost/scale* gate,
   because it can still fan out to many agents and spend real tokens.
5. **Reuse the durable substrate.** Track 2 is itself a Track-1 `@workflow`. It
   inherits durability, resume, the ledger, non-blocking launch, and gating
   machinery rather than reimplementing them.

## Architecture

### The interpreter is a durable `@workflow`

Track 2 is **one trusted, human-written `@workflow`** — `dynamic` — that takes a
validated JSON spec plus `kwargs` and interprets it by driving the existing
`WorkflowEngine` primitives (`spawn`, `send`, `close`, `parallel`,
`bash_predicate`, `enqueue`, `delegate`, `ask_human`, `checkpoint`).

Consequences:

- Durability, resume across `aegis --resume`, the JSONL ledger, non-blocking
  background launch, and the Track-1 approval/gating machinery are all
  **inherited**, not rebuilt.
- The only genuinely new surface is:
  1. the pydantic **spec models** + the **semantic validator**,
  2. the **interpreter loop** (walk node tree → resolve refs → dispatch → record),
  3. the **MCP tool** (`aegis_run_dynamic_workflow`) + the **cost gate**.

### Components and their boundaries

| Unit | Does | Depends on |
|---|---|---|
| `dsl/models.py` | pydantic models for every node + predicate + top-level spec; discriminated unions; structural validation | pydantic |
| `dsl/validate.py` | semantic validation pass (`.validate(spec, config)`) — reference resolution, upstream-only, list-source check, id uniqueness, acyclicity, profile/queue existence | models, config |
| `dsl/refs.py` | selector resolution (`<id>[.dotted.path]`) and `{{name}}` template substitution over a run-scoped output store | — (pure) |
| `dsl/interpreter.py` | the `dynamic` `@workflow`: walks the tree, resolves inputs, dispatches nodes to the engine, records outputs to the store + ledger | WorkflowEngine, models, refs |
| `dsl/plan.py` | resolve a spec to a **plan preview**: node graph + projected agent count + estimated spend (for the gate) | models |
| MCP tool | `aegis_run_dynamic_workflow(spec, kwargs, from_handle, callback)` — validate → gate → launch via WorkflowRunner | validate, plan, WorkflowRunner |

Each unit is independently testable: models/validate/refs/plan are pure and
hermetic; the interpreter is tested against a fake bridge like the existing
engine tests.

## Spec format

### Top level

```json
{
  "meta": { "name": "audit-routes", "description": "…" },
  "args_schema": { "…optional JSON Schema validating kwargs…" },
  "root": { "type": "sequence", "children": [ … ] }
}
```

- `meta.name` / `meta.description` — identity for the plan preview and any later
  save-as-command.
- `args_schema` — optional JSON Schema; `kwargs` are validated against it and
  exposed to templates as `{{args.x}}`.
- `root` — a single node, usually a `sequence`.

### Node catalog

**Structural**

- `sequence` — `{ "type": "sequence", "children": [<node>…] }`. Runs children in
  order. Output is an object keyed by each child's `id`.
- `parallel` — `{ "type": "parallel", "children": [<node>…] }`. Runs children
  concurrently; barrier (all complete before the node returns). Output keyed by
  child `id`.
- `map` — `{ "type": "map", "id": <str>, "over": <selector→list>, "body":
  <node>, "concurrency"?: <int> }`. Runs `body` once per element of the
  resolved list, with `{{item}}` and `{{index}}` in scope. Output is a list of
  the body's outputs, stored under the map's `id`. `concurrency` defaults to the
  workflow concurrency cap.

**Control** (each carries a typed predicate)

- `loop` — `{ "type": "loop", "id": <str>, "body": <node>, "until":
  <predicate>, "max_rounds": <int> }`. Runs `body`, evaluates `until` after each
  round; stops when `until` is true or `max_rounds` is reached. `max_rounds` is
  **mandatory**. Output is a list of per-round body outputs; `<id>.last`
  selects the final round.
- `if` — `{ "type": "if", "cond": <predicate>, "then": <node>, "else"?: <node>
  }`. Evaluates `cond`; runs `then` or (optional) `else`. Output is the taken
  branch's output.

**Leaf**

- `agent` — `{ "type": "agent", "id"?: <str>, "prompt": <template>, "target"?:
  <target>, "schema"?: <json-schema>, "inputs"?: { <name>: <selector> } }`.
  - `target` (discriminated union; default `spawn`):
    - `{ "kind": "spawn", "profile": <str> }` — fresh isolated agent of a
      configured profile (profile selects harness + model → cross-provider).
    - `{ "kind": "session", "handle": <str> }` — send to an existing **live
      named** agent (a running tab).
    - `{ "kind": "queue", "queue": <str> }` — delegate to a named queue (capped
      worker pool → backpressure).
  - `schema` — optional JSON Schema; with it, output is validated structured
    data (which is what makes selectors into this node's output reliable).
    Without it, output is free text.
  - `inputs` — bindings substituted into `prompt` as `{{name}}`.
- `human` — `{ "type": "human", "id"?: <str>, "question": <template>, "schema"?:
  <json-schema> }`. Pauses the run and asks the **operator via the TUI** (not
  Telegram in v1); returns the operator's structured choice. Enables interactive
  gated pipelines (e.g. design-thinking phase gates and back-transitions).

**Predicate** (sub-object of `loop.until` and `if.cond`)

- `{ "kind": "shell", "cmd": <str>, "cwd"?: <str>, "timeout"?: <int> }` — true
  iff the command exits 0. Deterministic and reproducible. Reuses
  `bash_predicate` semantics.
- `{ "kind": "judge", "condition": <NL string>, "inputs"?: [<selector>…] }` — an
  agent evaluates the natural-language `condition` against the referenced inputs
  (default: the body's last result) and returns `{ "decision": <bool>,
  "reason"?: <str> }`.

**Reduction / computation** has no dedicated node. To merge, dedup, or rank,
use an `agent` node whose `inputs` bind a `map`/`parallel` output. Selectors
select; agents compute.

### Data flow and references

- A node's output is referenceable only if it carries an `id`. `map` and `loop`
  **require** an `id` (their collected output is the useful value); a `map`/`loop`
  **body** and any one-off leaf may omit it. `sequence` / `parallel` key their
  output object by the `id`s of children that have one. Outputs are recorded
  under their `id` in a run-scoped store and the ledger.
- A **reference** is `<node-id>` (the whole output) or `<node-id>.dotted.path`
  (navigation into structured output — **no operators, no filters, no
  arithmetic**).
- **Template substitution** `{{name}}` in a `prompt` or `question` resolves a
  name bound in the node's `inputs`, or `args`, or `item` / `index` (inside a
  `map` body). Substitution only — no logic inside the braces.
- `map.over` and judge `inputs` are selectors and obey the same rules.

## Validation

Two layers, both **before execution**:

1. **Structural (pydantic, at the MCP tool boundary).** Discriminated-union
   node and predicate and target types; required fields present; `schema`
   fields are valid JSON Schema; every `loop` has an integer `max_rounds`. A
   malformed spec is rejected with the schema error and the model retries —
   "validation for free" at the tool-call layer.
2. **Semantic (`validate(spec, config)` pre-run).**
   - Every reference (`<id>[.path]`) resolves to a **declared upstream** node —
     no forward references, no reference to a node that has not run.
   - `map.over` resolves to a source that will be a list.
   - No `id` collisions; the node graph is acyclic.
   - Referenced `spawn.profile` and `queue.queue` exist in config.
     `session.handle` is **deferred to runtime** (live tabs are dynamic) and
     produces a clean runtime error if absent when reached.

A spec passing both layers is safe by construction: it contains no code, only
the node types above, and every reference is known-resolvable.

## Execution and durability

- The `dynamic` interpreter runs as a **background, non-blocking** workflow via
  `WorkflowRunner`: the MCP tool returns a `run_id` immediately; the final
  result lands in the caller's inbox when `callback` is true.
- `map` and `parallel` are bounded by the existing per-workflow concurrency cap.
- **Durability:** the validated spec JSON plus the per-node output ledger persist
  to the run's state directory (`.aegis/state/workflows/<run_id>/`). Because the
  spec is data, there is no "persist the script" problem — the instance *is*
  serialized.
- **Resume:** on `aegis --resume`, the interpreter replays recorded node outputs
  **and recorded `loop`/`if`/judge decisions**, re-enters at the first
  incomplete node, and restarts any node that was in-flight. Nondeterministic
  judge predicates therefore never break resume: the path is stable for every
  step already executed; only the interrupted step re-evaluates.

## Gating

One rule across both tracks; the *content* of the approval differs by track.

| | Track 1 (`@workflow` Python) | Track 2 (JSON DSL) |
|---|---|---|
| Operator-invoked (`/workflow`) | implicit approval | implicit approval |
| Agent-invoked (MCP) | **always prompts** | **prompts above a threshold** |
| Prompt shows | the **Python script** (code review) | the **resolved plan**: node graph + projected agent count + est. spend |
| Reason for the gate | code trust | cost / scale |

- **Track-2 auto-approve threshold** (config, e.g. `dynamic_workflow_autoapprove_agents: 5`).
  An agent-launched dynamic workflow whose plan projects at or below the
  threshold runs without prompting; above it, the operator is prompted with the
  plan preview. Below-threshold prompting is friction with no safety payoff,
  since a validated data spec carries no code risk.
- Track 1 has **no** such threshold: agent-authored Python always prompts,
  because the risk is code, and a scale threshold does not reduce code risk.

## Invocation surface

- **MCP:** `aegis_run_dynamic_workflow(spec, kwargs, from_handle, callback=true)`.
  `spec` is the inline JSON DSL body; pydantic validates at the boundary.
  Non-blocking; returns `{ run_id, status: "running" }`; result delivered to the
  caller's inbox tagged `workflow:<name>` when `callback` is true.
- **Operator:** `/workflow run <saved-name>` or pasting a spec; operator
  invocation is implicitly approved.
- **Status / cancel:** the existing `aegis_workflow_status` /
  `aegis_workflow_cancel` tools apply unchanged (Track 2 runs are ordinary
  workflow runs).

## Relationship to Track 1

This document **reframes but does not delete** the 2026-05-22 catalog. Track 1
remains the durable Python `@workflow` substrate. What changes:

- The catalog's original justification ("harnesses can't orchestrate, so ship
  reusable fan-out patterns as Python") is retired — intra-harness fan-out is
  now vendor-owned. Track 1's enduring role is the **computational / stateful /
  interactive** workflows that data cannot express (accumulators, arbitrary
  transforms, complex state machines).
- Track 1 gains the **gating rule** stated here (operator implicit / agent
  prompts, showing the script). This is the "gating is missing" piece; the rest
  of Track 1 already exists.
- **Composition:** a Track-1 conductor may launch a Track-2 fan-out (e.g. a
  design-thinking `prototype` phase fires a Track-2 audit). Track-2 does **not**
  launch Track-2 in v1 (flat); recursion lives in Track 1.

## Worked examples

### Fan-out audit with reduction (the sweet spot)

```json
{ "meta": { "name": "audit-routes", "description": "Audit route handlers for missing auth" },
  "root": { "type": "sequence", "children": [
    { "type": "agent", "id": "list", "prompt": "List every .ts file under src/routes/.",
      "schema": { "type": "object", "required": ["files"],
                  "properties": { "files": { "type": "array", "items": { "type": "string" } } } } },
    { "type": "map", "id": "audits", "over": "list.files",
      "body": { "type": "agent", "prompt": "Audit {{item}} for missing auth checks. Return findings.",
                "schema": { "type": "object", "required": ["findings"],
                            "properties": { "findings": { "type": "array" } } } } },
    { "type": "agent", "id": "report", "inputs": { "all": "audits" },
      "prompt": "Merge, dedup, and rank these per-file findings:\n{{all}}" }
  ] } }
```

### Keep fixing until a deterministic check passes

```json
{ "meta": { "name": "fix-types", "description": "Fix TS errors until tsc passes" },
  "root": { "type": "loop", "id": "rounds", "max_rounds": 4,
    "until": { "kind": "shell", "cmd": "npx tsc --noEmit" },
    "body": { "type": "agent", "prompt": "Run `npx tsc --noEmit`, fix the reported errors." } } }
```

### Interactive gated pipeline (design-thinking, abbreviated)

```json
{ "meta": { "name": "design-thinking", "description": "5-phase gated pipeline" },
  "root": { "type": "sequence", "children": [
    { "type": "agent", "id": "understand", "target": { "kind": "spawn", "profile": "analyst" },
      "prompt": "Run the understand phase for: {{args.problem}}. Write docs/dt/context-brief.md.",
      "schema": { "type": "object", "required": ["summary"], "properties": { "summary": { "type": "string" } } } },
    { "type": "human", "id": "gate1", "question": "Understand done → context-brief.md. Proceed or revise?",
      "schema": { "type": "string", "enum": ["proceed", "revise"] } },
    { "type": "if",
      "cond": { "kind": "judge", "condition": "the operator chose to proceed", "inputs": ["gate1"] },
      "then": { "type": "agent", "id": "define", "target": { "kind": "spawn", "profile": "analyst" },
                "inputs": { "brief": "understand.summary" },
                "prompt": "Run the define phase from:\n{{brief}}\nWrite docs/dt/spec.md." } }
  ] } }
```

This is authored as validated data, so an agent can generate it, and it is
durable and resumable — while a Claude dynamic workflow cannot pause for the
human at `gate1` at all.

## Testing strategy

- **Pure units (hermetic):** `models` (round-trip valid/invalid specs), `refs`
  (selector + template resolution over a fixture store), `validate`
  (upstream-only, list-source, cycles, id collisions, missing profile), `plan`
  (projected agent count on representative specs).
- **Interpreter (fake bridge):** drive the `dynamic` workflow against the same
  fake substrate the existing engine tests use — assert each node dispatches the
  right primitive, outputs record under the right ids, `map`/`parallel`
  fan-out, `loop` honors `max_rounds`, `if` routes on both predicate kinds,
  `human` awaits and records the choice.
- **Durability:** interrupt mid-run, resume, assert completed node outputs and
  loop/if decisions replay and only the in-flight node re-runs.
- **Gate:** projected-count threshold triggers prompt vs auto-approve;
  operator-invoked path is implicit.
- **Live (marked, opt-in):** a small real fan-out spec against a real `claude`
  subprocess, mirroring the existing live-test discipline.

## v1 non-goals (YAGNI)

- No arithmetic / filtering / computation in the DSL — agent nodes compute.
- Selectors navigate, never compute.
- No `group` target, `wait_any`, or cancel-losers (`map` + agent reduction
  covers fan-out + gather). Reachable later if a case needs `wait_any` semantics
  `map` cannot express.
- No Track-2-launches-Track-2 nesting; composition is Track-1 orchestrating
  Track-2.
- `human` node is TUI-only (no Telegram).
- Ephemeral only; saving a DSL run as a named `/command` (as validated data, no
  approval-to-run) is reachable-later.

## Open questions

- **Plan-preview cost estimate fidelity.** Projected agent count is exact for
  static shapes but an *upper bound* for `loop` (uses `max_rounds`) and unknown
  for `map.over` lengths not yet computed. v1 uses the static upper bound and
  labels it as such in the prompt; refine if operators find it misleading.
- **Judge context budget.** A `judge` predicate with large `inputs` could carry
  a heavy context. v1 passes referenced outputs verbatim; a summarization step
  is reachable-later if this bites.
- **No equality predicate.** Because selectors *select but never compute*, there
  is no operator to branch on a known value — so the common case "the operator
  chose `proceed`" is expressed as a `judge` predicate (an agent call) rather
  than a comparison, as in the design-thinking example. This is consistent with
  the design but spends an agent call on a trivial check. A third predicate kind
  `{ "kind": "equals", "left": <selector>, "right": <literal> }` — one fixed
  comparison, not a combinable operator set, so arguably still "not a language" —
  would make it deterministic and free. Deferred pending a decision, because it
  is the first crack toward an expression language and the design-thinking gate
  is exactly where it would be felt most.
