"""Canonical TDD-on-a-plan-step workflow.

Usage in .aegis.py:

    from examples.tdd_step import tdd_step    # noqa: F401 — registers

Then either CLI:

    aegis workflow run tdd_step \\
        --plan-step="VS1 inbox" \\
        --test-command="uv run pytest -k inbox" \\
        --test-path="tests/test_inbox.py"

Or via MCP from any agent:

    aegis_run_workflow(name="tdd_step",
                       kwargs={"plan_step": "...", "test_command": "...",
                               "test_path": "tests/test_inbox.py"},
                       from_handle="<your-handle>")
"""
from __future__ import annotations

from aegis.workflow import workflow, WorkflowError


@workflow
async def tdd_step(engine, *, plan_step: str,
                   test_command: str = "uv run pytest",
                   test_path: str = "tests/test_step.py"):
    """Run one TDD cycle. Subject = caller (if MCP-invoked) or a fresh
    queue worker (if CLI-invoked). Returns when tests are green;
    raises WorkflowError on hard failure (predicate violated)."""
    subject = engine.host or await engine.spawn("worker-sonnet")
    spawned = subject != engine.host
    try:
        # 1. Write failing tests at the known path.
        await engine.send(
            subject,
            f"Write failing tests at {test_path} for: {plan_step}. "
            f"Cover the spec.")
        await engine.drain(subject)

        # 2. Verify they fail.
        proc = await engine.bash(f"{test_command} {test_path}")
        if proc.returncode == 0:
            raise WorkflowError(
                f"tests at {test_path} passed without implementation")

        # 3. Implement.
        await engine.send(
            subject,
            f"Make {test_path} pass.\n\nFailing output:\n{proc.stdout}")
        await engine.drain(subject)

        # 4. Verify pass; retry up to 3 with feedback.
        for attempt in range(3):
            proc = await engine.bash(f"{test_command} {test_path}")
            if proc.returncode == 0:
                engine.log(f"green after {attempt + 1} attempt(s)")
                return f"green: {test_path}"
            await engine.send(
                subject,
                f"Still failing:\n{proc.stdout}\n\nFix.")
            await engine.drain(subject)
        raise WorkflowError(
            f"tests still red after 3 attempts: {plan_step}")
    finally:
        if spawned:
            await engine.close(subject)
