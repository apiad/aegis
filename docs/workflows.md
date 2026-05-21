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
  from_handle)`

## Hello workflow

```python
from aegis.workflow import workflow

@workflow
async def hello(engine, *, who: str = "world"):
    """Say hi via the default agent and return the reply."""
    handle = await engine.spawn("default")
    try:
        engine.send(handle, f"Say hello to {who} in one short sentence.")
        msgs = await engine.drain(handle)
        return msgs[-1].body if msgs else ""
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
aegis workflow run hello --who="Alex"
```

## The engine

The first parameter of every workflow is `engine: WorkflowEngine`.
This is your handle into the substrate. Key methods:

| Method | What it does |
|---|---|
| `engine.spawn(agent_slug) -> handle` | Spawn a fresh session of an agent profile; return its handle. |
| `engine.send(handle, text)` | Send a user-message turn into a session's inbox. Non-blocking. |
| `engine.drain(handle) -> list[InboxMessage]` | Wait for the session to finish its current turn(s) and return everything it produced. |
| `engine.delegate(queue, payload) -> str` | Enqueue on a named queue; wait for the worker callback; return the worker's final text. |
| `engine.close(handle)` | Close a session. |
| `engine.bash(cmd, …) -> CompletedProcess` | Run a shell command. Useful for predicates (running tests, checking files). |
| `engine.log(message)` | Write to the workflow's JSONL log. |
| `engine.caller_handle` | If MCP-invoked, the handle of the agent that invoked you. `None` if CLI-invoked. |
| `engine.list_sessions()` / `engine.list_agents()` | Read-only substrate views. |

Workflows are `async def` — `await` everything that returns a coroutine.

## TDD example (shipped)

`examples/tdd_step.py` is a canonical workflow showing predicate +
retry-with-feedback:

```python
from aegis.workflow import workflow, WorkflowError

@workflow
async def tdd_step(engine, *, plan_step: str,
                   test_command: str = "uv run pytest",
                   test_path: str = "tests/test_step.py"):
    """Run one TDD cycle on the caller (or a fresh worker)."""
    subject = engine.caller_handle or await engine.spawn("worker-sonnet")
    spawned = subject != engine.caller_handle
    try:
        # 1. Write failing tests at the known path.
        engine.send(subject,
            f"Write failing tests at {test_path} for: {plan_step}.")
        await engine.drain(subject)

        # 2. Verify they fail.
        proc = await engine.bash(f"{test_command} {test_path}")
        if proc.returncode == 0:
            raise WorkflowError(
                f"tests at {test_path} passed without implementation")

        # 3. Implement.
        engine.send(subject,
            f"Make {test_path} pass.\n\nFailing output:\n{proc.stdout}")
        await engine.drain(subject)

        # 4. Verify pass; retry up to 3 with feedback.
        for attempt in range(3):
            proc = await engine.bash(f"{test_command} {test_path}")
            if proc.returncode == 0:
                engine.log(f"green after {attempt + 1} attempt(s)")
                return f"green: {test_path}"
            engine.send(subject,
                f"Still failing:\n{proc.stdout}\n\nFix.")
            await engine.drain(subject)
        raise WorkflowError(
            f"tests still red after 3 attempts: {plan_step}")
    finally:
        if spawned:
            await engine.close(subject)
```

Import it in `.aegis.py` to register, then:

```bash
aegis workflow run tdd_step \
    --plan-step="VS1 inbox" \
    --test-command="uv run pytest -k inbox" \
    --test-path="tests/test_inbox.py"
```

Or from inside another agent:

```
aegis_run_workflow(name="tdd_step",
                   kwargs={"plan_step": "VS1 inbox",
                           "test_command": "uv run pytest -k inbox",
                           "test_path": "tests/test_inbox.py"},
                   from_handle="<my-handle>")
```

## Auto-drain and auto-close

The runner that invokes your workflow tracks every handle you
`spawn()` (the **spawned set**) and every handle you `send()` to (the
**touched set**). When your workflow returns:

- Touched sessions are auto-drained (up to a 30s timeout) so any
  in-flight turn isn't truncated.
- Spawned sessions are auto-closed.

You don't need defensive `try/finally` for those cases — but if you
mutate sessions you didn't spawn, you remain responsible for them.

## Failure model

- `WorkflowError` is the expected-failure path (predicate violated,
  retry exhausted). The runner reports it cleanly with the message
  you raised.
- Plain `Exception` is treated as an unexpected crash; full traceback
  goes into the JSONL log.

## Registration semantics

`@workflow` is **idempotent on reload**: re-importing the same module
(same file path + same line number) just rebinds the entry. Different
file with the same name is a hard collision and raises `ConfigError`
at registration time.

## When to write a workflow vs. when to just use queues

- **Just queues**: producer says "do this, tell me when done." No
  intermediate steps. No predicates. Single round-trip.
- **Workflow**: multiple steps with checks between them, retry loops,
  shell predicates (run the tests, check the file, grep the log), or
  multi-agent coordination ("ask reviewer; if the reply mentions X,
  hand off to implementer").
