# Workflow Catalog — design spec

**Date:** 2026-05-22
**Status:** Draft, approved
**Owner:** Alex + Claude

## Summary

`aegis.workflows` becomes a real catalog package shipping ready-to-use
workflows for common multi-agent patterns (brainstorming, plan execution,
code review, TDD loops, etc.). The existing v1 `@workflow` scaffold is
extended with the primitives the catalog needs: unified handle-based
dispatch (`engine.send(handle, …)`), human-in-the-loop (`engine.ask_human`),
explicit checkpoints with durable resume, subagent spawn/close, parallel
fan-out, and predicate-retry loops. Workflows always run **on** a host
agent — the agent that invoked them, or the default agent if launched
from the CLI. The host's tab is the workflow's UI; spawned subagents
are backstage.

## Motivation

The v1 workflow scaffold (`@workflow` + `WorkflowEngine` + `aegis
workflow list/run` CLI + `aegis_run_workflow` MCP tool) is a foundation
but it ships zero workflows. Every team using aegis has to write their
own — there's no shared catalog of the patterns we already use day to
day (`brainstorm_to_spec`, `execute_plan`, `review_branch`, …).

The catalog also pins down the engine API: each pattern has known
primitive needs, and the design of `WorkflowEngine` follows from what
those patterns require, not from speculation.

This spec defines:
1. The extended `WorkflowEngine` API (the primitives the catalog needs).
2. The runtime model (workflows on a host agent, with subagents).
3. The packaging convention (`aegis.workflows.<name>`).
4. The configuration shape in `.aegis.py`.
5. Four seed workflows: `brainstorm_to_spec`, `execute_plan`,
   `review_branch`, `tdd_cycle`.

## Non-goals (v1)

- **Full durable execution (Temporal-style replay).** v1 uses explicit
  checkpoints written by the workflow author; auto-journaling every
  `await` is a future evolution.
- **Spec-language workflows.** Workflows are Python only in v1.
  Markdown/YAML-driven workflows are a separate roadmap item.
- **Cross-host workflow distribution.** A workflow runs on a single
  aegis process. Multi-host is gated on the daemon project.
- **Workflow-to-workflow composition primitives.** A workflow can
  `engine.subagent()` another workflow by name (the subagent runs
  `aegis_run_workflow`), but there's no first-class
  `engine.invoke_workflow()` primitive in v1.
- **Visual workflow tabs.** No new TUI tab type. Workflows narrate
  into the host agent's existing transcript.

## Design

### Runtime model — workflows always have a host

Every workflow run has a **host agent**. There are two launch paths:

- **MCP launch.** An agent calls `aegis_run_workflow(name=…, kwargs=…)`.
  The caller IS the host. The MCP tool returns immediately with
  `{workflow_id, status: "running"}`; the workflow runs in a background
  asyncio task owned by the new `WorkflowRunner`. Narration appears in
  the host's transcript via aegis-internal injection (the same
  pathway inbox messages use).

- **CLI launch.** `aegis workflow run <name> [--on <handle>] [<kwargs>]`.
  Without `--on`, aegis spawns the default agent and uses it as host
  (auto-closing it after the workflow ends, unless `--keep-host`).
  With `--on <handle>`, the specified live agent is the host.

The host's tab is the workflow's UI. Subagents are backstage by
default (no tab).

### MCP semantics — non-blocking trigger, structured polling

`aegis_run_workflow` is **non-blocking** in v1. It returns immediately:

```json
{"workflow_id": "wf_01HK…", "host": "lucid-knuth", "status": "running"}
```

The caller (the host agent) can then:
- Continue with other work (the workflow runs concurrently in the
  background — narration in the same transcript).
- Optionally poll status via `aegis_workflow_status(workflow_id)` → `{phase,
  step, percent, last_checkpoint, last_log, finished_at, result}`.
- Optionally cancel via `aegis_workflow_cancel(workflow_id)`.

This is the only model that works given the MCP-tool blocking
constraint. A blocking `aegis_run_workflow` would prevent the workflow
from calling `engine.send(engine.host, …)` (the host would be stuck
inside the tool call and unable to respond). Non-blocking trigger
unbinds the workflow from the host's turn cycle.

When the workflow finishes, it appends a final narration block to the
host's transcript with the result (and a structured payload retrievable
via `aegis_workflow_status`).

### Engine API

```python
class WorkflowEngine:
    # Identity ----------------------------------------------------
    host: Handle                     # invoking agent's handle; always present
    workflow_id: str                 # this run's id
    name: str                        # workflow registered name

    # Thinking ---------------------------------------------------
    async def send(handle: Handle, prompt: str, *, timeout: float | None = None) -> str:
        """Send `prompt` as a user-turn to any handle (host or subagent),
        wait for the agent's next assistant message, return its text."""

    async def ask_human(question: str, *, options: list[str] | None = None,
                        timeout: float | None = None) -> str:
        """Prompt the user via the host tab's input bar. Returns the user's reply."""

    # Subagents --------------------------------------------------
    async def spawn(profile: str, *, alias: str | None = None) -> Handle:
        """Spawn a fresh isolated subagent of `profile`. Returns its handle.
        Auto-closed on workflow end unless explicitly closed earlier."""

    async def close(handle: Handle) -> None:
        """Close a spawned subagent. Errors if handle is the host."""

    # Durability -------------------------------------------------
    async def resume_state(self) -> dict | None:
        """At workflow start: returns the payload of the last checkpoint
        if resuming from a prior run; None if fresh."""

    async def checkpoint(name: str, payload: dict) -> None:
        """Append a checkpoint to the workflow's ledger. `payload` is the
        author-defined state needed to resume here."""

    # Shell ------------------------------------------------------
    async def bash(cmd: str, *, cwd: str | None = None, timeout: float | None = None
                   ) -> dict:                          # {stdout, stderr, exit}
        ...

    async def bash_predicate(cmd: str, *, retry_with: str | Callable[[dict], str],
                             max_retries: int = 3) -> dict:
        """Run `cmd`; if exit ≠ 0, format `retry_with` (string template with
        {stdout}/{stderr}, or callable on the bash result) and call
        `engine.send(self.host, retry_with)` to ask the host to fix it.
        Re-run `cmd`. Repeat up to `max_retries` times. Returns the final
        bash result; raises `PredicateFailed` if still failing."""

    # Concurrency ------------------------------------------------
    async def parallel(coros: list[Awaitable]) -> list:
        """asyncio.gather with per-branch logging into host transcript.
        Branch labels derived from the awaitable's __name__/repr."""

    # Primitives passthrough -------------------------------------
    async def canvas_open(name: str, file: str | None = None) -> dict
    async def canvas_write(name: str, section: str, content: str) -> dict
    async def canvas_read(name: str, section: str | None = None) -> str
    async def term_spawn(name: str, *, shell: str | None = None,
                         cwd: str | None = None) -> dict
    async def term_run(name: str, cmd: str, *, timeout: float | None = None) -> dict
    async def queue_enqueue(queue: str, payload: str) -> dict

    # Narration --------------------------------------------------
    async def log(msg: str) -> None:
        """One-line narration in the host's transcript. Tagged with
        the workflow's name + current phase."""
```

`Handle` is the existing aegis handle type (a string slug like
`lucid-knuth`).

#### `engine.send` semantics in detail

`engine.send(handle, prompt)` puts `prompt` into the addressed handle's
input queue as a user-turn (same plumbing inbox messages use), then
awaits the next *complete* assistant message from that handle. "Complete"
means: the assistant's turn ends (no more tool calls pending; final
assistant text emitted). Returns the assistant text.

Works identically for `engine.host` and spawned subagents. The host
case is special only in that the user can also see what's happening
(host's transcript is live in the TUI); subagent case is invisible
unless `--show-subagents` was passed.

If a workflow calls `engine.send(engine.host, …)` while the user is
typing into the host tab, the user's in-progress input is preserved
(buffered); the workflow's prompt appears as an injected user-turn
(visually distinguished — `🤖 workflow:<name>` prefix). The host's
response goes back to the workflow.

#### `engine.ask_human` UX

When called:
1. The host's tab input bar enters "workflow question" mode with a
   visual marker: `? <question>  ↵ to send`.
2. If `options=[...]` was passed, options are listed below the input
   bar; user can type the option text or its index.
3. The user's next Enter sends the line to the workflow's pending
   future; the input bar returns to normal mode.
4. Anything the user types before `ask_human` is invoked stays in the
   buffer (not consumed by the workflow); anything after, until they
   Enter, becomes the reply.

In headless mode: the question is sent via Telegram (with structured
options as inline keyboard if `options` was passed). The next inbound
Telegram message tagged for the workflow becomes the reply. A pending
`ask_human` blocks the workflow indefinitely (no default timeout)
unless `timeout` was passed.

### Durability — explicit checkpoints

The engine writes `.aegis/state/workflows/<workflow_id>/`:

```
meta.json     # {workflow_id, name, host, started_at, kwargs, status, version}
ledger.jsonl  # one record per checkpoint or significant event
```

Ledger record types:

```json
{"kind": "started",     "at": "...", "kwargs": {...}}
{"kind": "checkpoint",  "at": "...", "name": "after_research",
                        "payload": {...}}
{"kind": "spawn",       "at": "...", "subagent": "brisk-curie",
                        "profile": "implementer"}
{"kind": "close",       "at": "...", "subagent": "brisk-curie"}
{"kind": "finished",    "at": "...", "result": ...}
{"kind": "errored",     "at": "...", "error": "..."}
```

On `aegis --resume`, the `WorkflowRunner` reads each workflow's
ledger:
- If `finished` or `errored` is present, the run is terminal — load
  result for `aegis_workflow_status` but do not restart.
- Otherwise, the workflow is restarted: the workflow function is
  re-invoked from the top, with the engine's `resume_state()` returning
  the last `checkpoint` payload. The author's responsibility is to
  check this state and skip already-done work.

Subagents do not survive restart. The workflow must re-spawn any
needed subagents after resuming (or skip work that was attributed to
them via the checkpoint state).

`aegis --clean` skips workflow restoration entirely.

### Catalog package layout

```
src/aegis/workflows/
  __init__.py                 # re-exports the 4 seeds (import = registration)
  brainstorm_to_spec.py
  execute_plan.py
  review_branch.py
  tdd_cycle.py
  _lib/
    __init__.py
    plan_parser.py            # parse markdown plan → task list
    spec_renderer.py          # dialogue + answers → spec doc
    git_helpers.py            # diff vs main, branch info
    options.py                # menu/option formatting for ask_human
```

`from aegis.workflows import brainstorm_to_spec` is enough to register
the workflow (the `@workflow("brainstorm_to_spec")` decorator runs as
a side effect of importing the module). Once registered, the workflow
appears in `aegis_run_workflow` discovery and in `aegis workflow list`.

### Configuration in `.aegis.py`

```python
from aegis.workflows import (
    brainstorm_to_spec, execute_plan, review_branch, tdd_cycle,
)
# Importing is enough to register.

execute_plan.configure(
    default_subagent_profile="implementer",
    max_parallel_tasks=1,
)
review_branch.configure(
    reviewers=["security-reviewer", "api-reviewer", "test-reviewer"],
    base_branch="main",
)
```

`.configure(**kwargs)` is a thin attribute setter on the decorated
workflow object. The workflow body reads its configuration via
`engine.config` (an immutable dict — defaults overlaid with `.configure`
overrides overlaid with runtime kwargs).

### CLI launch

```
aegis workflow list
aegis workflow run <name> [--on <handle>] [--keep-host] [--show-subagents]
                          [--headless] [-- <kw=v> <kw=v> …]
aegis workflow status <workflow_id>
aegis workflow cancel <workflow_id>
```

`-- <kw=v>` after `--` separator are passed as `kwargs` to the workflow.

### Seed catalog — sketches

#### 1. `brainstorm_to_spec`

Interactive dialogue with the user that produces a spec doc.

```python
@workflow("brainstorm_to_spec")
async def brainstorm_to_spec(engine, *, topic: str | None = None) -> str:
    state = await engine.resume_state() or {"phase": "topic", "answers": {}}
    if state["phase"] == "topic":
        topic = topic or await engine.ask_human(
            "What are we brainstorming about?")
        state = {"phase": "questions", "topic": topic, "answers": {}, "idx": 0}
        await engine.checkpoint("topic_set", state)

    questions = [
        "What's the problem this solves?",
        "Who is this for?",
        "What's the smallest version that's useful?",
        "What approaches have you considered?",
        "What's out of scope?",
    ]
    while state["idx"] < len(questions):
        q = questions[state["idx"]]
        ans = await engine.ask_human(q)
        state["answers"][q] = ans
        state["idx"] += 1
        await engine.checkpoint(f"q_{state['idx']}", state)

    if state.get("spec_path") is None:
        writer = await engine.spawn("spec_writer")
        spec_text = await engine.send(writer, render_spec_prompt(
            topic=state["topic"], answers=state["answers"]))
        slug = slugify(state["topic"])
        date = today_iso()
        path = f"docs/superpowers/specs/{date}-{slug}-design.md"
        write_file(path, spec_text)
        state["spec_path"] = path
        await engine.checkpoint("spec_written", state)
        await engine.close(writer)

    await engine.log(f"Spec written to {state['spec_path']}")
    return state["spec_path"]
```

Uses: `ask_human`, `spawn`, `send`, `close`, `checkpoint`, `resume_state`,
`log`. Zero use of `engine.host` for thinking — the host is purely the
dialogue surface.

#### 2. `execute_plan`

Read a markdown plan, drive its tasks to completion via subagents,
checkpoint after each task.

```python
@workflow("execute_plan")
async def execute_plan(engine, *, plan_path: str) -> str:
    state = await engine.resume_state() or {"phase": "init", "done": []}
    if state["phase"] == "init":
        plan = parse_plan(plan_path)
        state = {"phase": "tasks", "plan_path": plan_path,
                 "tasks": [{"id": t.id, "title": t.title, "body": t.body}
                           for t in plan.tasks],
                 "done": []}
        await engine.checkpoint("parsed", state)

    profile = engine.config.get("default_subagent_profile", "implementer")
    for task in state["tasks"]:
        if task["id"] in state["done"]:
            continue
        await engine.log(f"▶ task {task['id']}: {task['title']}")
        impl = await engine.spawn(profile, alias=f"impl-{task['id']}")
        try:
            await engine.send(impl, task_prompt(task))
            # Verification: run test predicate if the task declares one
            if "verify" in task:
                await engine.bash_predicate(
                    task["verify"],
                    retry_with=f"Verification failed for task {task['id']}. "
                               "Output:\n{stdout}\n{stderr}\nPlease fix.",
                    max_retries=2,
                )
        finally:
            await engine.close(impl)
        state["done"].append(task["id"])
        await engine.checkpoint(f"task_{task['id']}", state)

    return f"completed {len(state['done'])}/{len(state['tasks'])} tasks"
```

Uses: `resume_state`, `spawn`, `send`, `bash_predicate`, `close`,
`checkpoint`, `log`, `engine.config`.

#### 3. `review_branch`

Run multiple reviewers in parallel against the current branch's diff.

```python
@workflow("review_branch")
async def review_branch(engine, *, base: str = "main") -> str:
    diff_result = await engine.bash(f"git diff {base}...HEAD")
    diff = diff_result["stdout"]
    if not diff.strip():
        return "no diff vs base"

    reviewers = engine.config.get("reviewers",
                                  ["security-reviewer", "api-reviewer",
                                   "test-reviewer"])

    async def one_review(profile: str) -> tuple[str, str]:
        r = await engine.spawn(profile, alias=f"r-{profile.split('-')[0]}")
        try:
            return profile, await engine.send(r, review_prompt(profile, diff))
        finally:
            await engine.close(r)

    results = await engine.parallel([one_review(p) for p in reviewers])

    report = render_review_report(results)
    path = f"docs/reviews/{today_iso()}-{branch_slug()}.md"
    write_file(path, report)
    await engine.log(f"Review written to {path}")
    return path
```

Uses: `bash`, `engine.config`, `spawn`, `send`, `close`, `parallel`,
`log`. No checkpointing — the workflow is short and the parallel join
is the only natural checkpoint, after which the work is essentially
done.

#### 4. `tdd_cycle`

Looped predicate retry: write failing test → fail → impl → pass →
review.

```python
@workflow("tdd_cycle")
async def tdd_cycle(engine, *, feature: str, test_path: str) -> str:
    state = await engine.resume_state() or {"phase": "write_test"}

    if state["phase"] == "write_test":
        impl = await engine.spawn("implementer", alias="tdd-impl")
        await engine.send(impl, f"Write a failing test for: {feature}\n"
                                f"Put it at {test_path}.")
        # Test should fail because feature is not built
        await engine.bash_predicate(
            f"uv run pytest {test_path} 2>&1 | grep -E 'FAIL|ERROR'",
            retry_with=("The test you wrote at {} should FAIL because the "
                        "feature isn't built yet. Rewrite it so it fails.")
                       .format(test_path),
            max_retries=2,
        )
        state = {"phase": "implement", "impl_alias": "tdd-impl"}
        await engine.checkpoint("test_written", state)
        # Keep `impl` open across checkpoint — re-spawn on resume.

    if state["phase"] == "implement":
        impl = await engine.spawn("implementer", alias="tdd-impl")
        await engine.send(impl, f"Now implement the feature: {feature}\n"
                                f"Make the test at {test_path} pass.")
        await engine.bash_predicate(
            f"uv run pytest {test_path}",
            retry_with="Tests are still failing. Output:\n{stdout}\n{stderr}",
            max_retries=3,
        )
        state = {"phase": "review"}
        await engine.close(impl)
        await engine.checkpoint("implemented", state)

    if state["phase"] == "review":
        reviewer = await engine.spawn("reviewer")
        review = await engine.send(reviewer, f"Final review of {feature} "
                                             f"and its test at {test_path}.")
        await engine.close(reviewer)
        await engine.log(f"Review:\n{review}")

    return f"tdd_cycle complete for {feature}"
```

Uses everything. Demonstrates that `bash_predicate` is the key catalog
primitive — it's the loop with feedback that makes TDD-style
workflows possible.

### Architecture

New modules:

- `src/aegis/workflows/__init__.py` — re-exports + import-registration.
- `src/aegis/workflows/{brainstorm_to_spec,execute_plan,review_branch,tdd_cycle}.py` — the four seeds.
- `src/aegis/workflows/_lib/{plan_parser,spec_renderer,git_helpers,options}.py` — shared helpers.

Extended:

- `src/aegis/workflow/engine.py` — `WorkflowEngine` gains `ask_human`,
  `spawn`, `close`, `checkpoint`, `resume_state`, `bash_predicate`,
  `parallel`, `canvas_*`, `term_*`, `queue_enqueue`, `config`,
  `host`, `workflow_id`.
- `src/aegis/workflow/runner.py` — `WorkflowRunner`:
  - Owns running workflows (`dict[workflow_id, _RunningWorkflow]`).
  - Spawns each as an `asyncio.Task`.
  - Plumbs `engine.send` to the existing `InboxRouter` for delivery,
    and to a per-handle "next assistant message" awaiter (new
    bookkeeping in the session manager — when an agent emits a final
    assistant message, if a workflow is awaiting it, resolve the
    future).
  - Plumbs `engine.ask_human` to a per-host "workflow input queue"
    that the TUI consumes when the host is in "workflow question"
    mode (or to Telegram in headless).
  - Writes the ledger; reads it on `aegis --resume`.
- `src/aegis/mcp/server.py` — `aegis_run_workflow` becomes
  non-blocking (returns `{workflow_id, host, status}`); new tools
  `aegis_workflow_status`, `aegis_workflow_cancel`.
- `src/aegis/tui/agent_tab.py` (or wherever the input bar lives) —
  the input widget gains a "workflow question" mode triggered by a
  pending `ask_human`.
- `src/aegis/cli.py` — `aegis workflow run/list/status/cancel` CLI.
- `src/aegis/core/manager.py` — `SessionManager` gains
  `attach_workflow_runner`.
- `src/aegis/mcp/bridge.py` — `AppBridge` gains `workflow_runner`.

### Test plan

Hermetic tests:

**Engine primitives (`tests/test_workflow_engine_*.py`).** Stub
`InboxRouter`, `SessionManager`, and a `FakeAgent` that emits a
canned reply. Cover `send` (host + sub), `ask_human` (with a stub
"user replies after N seconds"), `spawn`/`close` lifecycle,
`checkpoint`/`resume_state` round-trip, `bash` / `bash_predicate`
with retry feedback, `parallel`.

**Runner (`tests/test_workflow_runner.py`).** Boot a `WorkflowRunner`
against fake bridge, run a tiny workflow, verify ledger contents,
restart and verify resume from last checkpoint.

**Seed workflows (`tests/test_workflow_catalog_*.py`).** One test
file per seed, exercising the happy path with stubbed subagents
and a synthetic ask_human reply queue. Verifies the workflow emits
the expected sequence of `spawn`/`send`/`checkpoint`/`ask_human`/
`close` calls and produces the expected artifact (spec file written,
plan tasks marked done, review report generated).

**MCP (`tests/test_workflow_mcp.py`).** `aegis_run_workflow` returns
non-blocking with correct shape; `aegis_workflow_status` reports
phase/percent; `aegis_workflow_cancel` cancels a running workflow.

**Persistence (`tests/test_workflow_persistence.py`).** Run, kill
mid-flight, restart, verify resume picks up at last checkpoint.

Smoke (manual, not CI):
- `aegis workflow run brainstorm_to_spec --topic="a test feature"` and
  walk through the dialogue.
- `aegis workflow run execute_plan --plan-path=<some plan>` and watch
  subagents fan out.

### Failure modes and edge cases

- **`engine.send(handle)` to a dead/closed agent.** Raises
  `AgentNotFound`. Workflow author responsibility to handle.
- **`ask_human` while another `ask_human` is pending on the same
  host.** Queue the second. Render both questions in order; the
  user's replies are matched FIFO.
- **Workflow function raises.** Engine catches; appends `errored`
  to ledger; emits final narration block with the exception; sets
  `aegis_workflow_status` to `errored`.
- **Checkpoint payload not JSON-serializable.** Raises immediately
  at `checkpoint()` call site so the author fixes it. No silent
  truncation.
- **Resume of a workflow whose code has changed since last run.**
  Author's responsibility — the engine cannot detect this. Best
  practice: include a `version` field in your state and bail if it
  doesn't match.
- **Cancel mid-flight.** `aegis_workflow_cancel` cancels the asyncio
  task. Engine appends `errored` to ledger with reason
  `cancelled_by_user`. In-flight subagents are closed; in-flight bash
  commands receive SIGTERM.
- **CLI launch when default agent isn't configured.** Errors with
  helpful message: "No default agent in .aegis.py — either configure
  `default_agent` or use `--on <existing-handle>`."

## References

- v1 scaffold: `WorkflowEngine` and `aegis_run_workflow` from
  `roadmap.md` v0.2.0 entry.
- [[2026-05-21-shared-canvas-design]] — `engine.canvas_*` passthrough
  surface.
- [[2026-05-21-live-terminals-design]] — `engine.term_*` passthrough
  surface.
- [[2026-05-21-session-persistence-design]] — the ledger pattern
  workflows borrow.
