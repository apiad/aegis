# Workflows

A **workflow** is a Python procedure that orchestrates one or more
agents through a deterministic sequence of steps. They sit one level
above queues: where a queue is a single fire-and-forget delegation,
a workflow is "delegate, run a predicate, retry with feedback if it
fails, then delegate something else."

Workflows are written once, registered with `@workflow`, and invoked
either:

- From the CLI: `aegis workflow run <name> [--kwarg=value …]`
- From any agent via MCP: `aegis_run_workflow(name, kwargs,
  from_handle)` — returns immediately with a `workflow_id`; the
  workflow keeps running in the background.

## Hello workflow

```python
from aegis.workflow import workflow

@workflow
async def hello(engine, *, who: str = "world") -> str:
    """Say hi via a fresh subagent and return its reply."""
    handle = await engine.spawn("default")
    try:
        return await engine.send(
            handle, f"Say hello to {who} in one short sentence.")
    finally:
        await engine.close(handle)
```

Register it by importing in `.aegis.py`:

```python
from my_workflows import hello   # noqa: F401
```

Run it:

```bash
aegis workflow list
aegis workflow run hello --who=Alex
```

## The engine

The first parameter of every workflow is `engine: WorkflowEngine` —
the substrate handle. Key methods:

| Method | What it does |
|---|---|
| `await engine.spawn(profile, *, alias=None) -> handle` | Spawn a fresh session of an agent profile. |
| `await engine.send(handle, text) -> str` | Send a turn into a session's inbox; await and return the final assistant message. |
| `await engine.ask_human(question, *, options=None) -> str` | Pause the workflow until the host (or operator) replies. |
| `await engine.close(handle)` | Close a subagent session. |
| `await engine.bash(cmd, *, cwd=None, timeout=None) -> _BashResult` | Run a shell command; supports both attribute and dict-style access. |
| `await engine.bash_predicate(cmd, *, retry_with, max_retries=3)` | Loop: run, on non-zero send `retry_with` feedback to the host and retry. Raises `PredicateFailed` when the budget is exhausted. |
| `await engine.parallel([coro, …]) -> list` | Run awaitables concurrently and join the results. |
| `await engine.checkpoint(name, payload)` | Persist a JSON-serializable state snapshot to the ledger. |
| `await engine.resume_state() -> dict \| None` | Return the last checkpoint payload, or `None` for a fresh run. |
| `engine.log(message)` | Append a line to the workflow's JSONL log. |
| `engine.config` | Read-only dict view of values from `.aegis.py`. |
| `engine.host`, `engine.workflow_id`, `engine.name` | Runtime identity. |

Workflows are `async def` — `await` everything that returns a coroutine.

## Catalog

The `aegis.workflows` package ships four seed workflows; importing the
package registers all four. They are designed to be both useful out of
the box and reference implementations for new workflows.

### `brainstorm_to_spec`

Interactive five-question dialogue with the host; spawns a `spec_writer`
subagent to synthesise the answers into a markdown spec under
`docs/superpowers/specs/`. Uses `ask_human`, `spawn`, `send`, `close`,
`checkpoint`, `resume_state`.

```bash
aegis workflow run brainstorm_to_spec --topic="rate limiting"
```

### `execute_plan`

Parse a plan markdown (`## Slice N — title` headings via
`plan_parser.parse_plan`), dispatch one `implementer` subagent per task,
checkpoint after each. A killed run resumes at the next unfinished task.

```bash
aegis workflow run execute_plan --plan_path=docs/plans/feature.md
```

### `review_branch`

Compute the diff vs a base ref, fan out reviewer subagents in parallel
(default: `security-reviewer`, `api-reviewer`, `test-reviewer`), and
write a structured markdown report under `docs/reviews/`.

```bash
aegis workflow run review_branch --base=main
```

### `tdd_cycle`

Three-phase predicate-driven loop: implementer writes a failing test
(asserted via `bash_predicate` grepping for `FAIL|ERROR`), implementer
makes it pass (asserted via plain pytest exit 0), reviewer inspects.
Checkpoints between phases.

```bash
aegis workflow run tdd_cycle --feature=rate_limit \
    --test_path=tests/test_rate_limit.py
```

## Durability

Each workflow run gets a directory at `.aegis/state/<workflow_id>/`
holding `meta.json` (name + kwargs + host) and `ledger.jsonl` (append-
only event log: every `checkpoint(...)` call, plus terminal `finished`
/ `errored` / `resumed` records).

On `aegis --resume`, the runner re-reads the ledger and re-invokes the
workflow; `engine.resume_state()` returns the most recent checkpoint
payload, letting the body skip phases it has already completed.

Checkpoint payloads must be JSON-serializable — `engine.checkpoint`
raises `TypeError` at the call site if not, so authors fix it
immediately.

## Failure model

- `WorkflowError` / `PredicateFailed` are the expected-failure path
  (predicate violated, retry exhausted). The runner records them in
  the ledger as `errored` and surfaces them to the caller.
- Plain `Exception` is treated as an unexpected crash; full traceback
  goes into the JSONL log.

## Configuration

Workflows read `engine.config` — populated from `.aegis.py`. For
example, to override `review_branch`'s reviewer set:

```python
# .aegis.py
workflows = {
    "review_branch": {
        "reviewers": ["security-reviewer", "perf-reviewer"],
    },
}
```

The `default_subagent_profile` key (used by `execute_plan`) defaults
to `"implementer"`.

## Visibility in the TUI

The `Ctrl+D` dashboard has a `WORKFLOWS` band (below `IN-FLIGHT`)
showing every workflow run the current process knows about — running
ones first, then recently-terminal ones in reverse-finish order:

```
WORKFLOWS
  ▶ tdd_cycle 0ABCDEF  · host lucid-knuth  · running  · 12.4s
  ? brainstorm_to_spec 1234AB  · host wry-hopper  · awaiting reply  · 47.2s
  ✓ review_branch 9988CC  · host lucid-knuth  · ok  · 3m02s
      → 3 reviews ok, 0 blocked
  ✗ execute_plan 7766DD  · host brisk-curie  · error  · 1m18s
      → PredicateFailed: tests still failing
```

The band reads `bridge.workflow_runner.snapshot()` directly. Elapsed
time is refreshed once per second; a workflow waiting on
`engine.ask_human(...)` shows up as `awaiting reply`.

## When to write a workflow vs. just use queues

- **Just queues**: producer says "do this, tell me when done." No
  intermediate steps. No predicates. Single round-trip.
- **Workflow**: multiple steps with checks between them, retry loops,
  shell predicates (run the tests, check the file, grep the log),
  multi-agent coordination, or anything that should survive a
  process restart via checkpoints.
