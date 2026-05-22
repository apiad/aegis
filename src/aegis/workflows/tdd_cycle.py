"""tdd_cycle — three-phase predicate-driven TDD loop.

Phase 1 (write_test): implementer drafts a failing test; predicate
asserts the suite contains FAIL/ERROR. Phase 2 (implement): same
implementer makes the test pass; predicate is plain pytest exit 0.
Phase 3 (review): reviewer subagent inspects feature + test.

Each phase ends with a checkpoint so the workflow resumes mid-cycle.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow("tdd_cycle")
async def tdd_cycle(engine, *, feature: str, test_path: str) -> str:
    state = await engine.resume_state() or {"phase": "write_test"}

    if state["phase"] == "write_test":
        impl = await engine.spawn("implementer", alias="tdd-impl-test")
        try:
            await engine.send(
                impl,
                f"Write a failing test for: {feature}\n"
                f"Put it at {test_path}.")
            await engine.bash_predicate(
                f"uv run pytest {test_path} 2>&1 | grep -E 'FAIL|ERROR'",
                retry_with=(f"The test you wrote at {test_path} should FAIL "
                            "because the feature isn't built yet. Rewrite "
                            "it so it fails."),
                max_retries=2)
        finally:
            await engine.close(impl)
        state = {"phase": "implement"}
        await engine.checkpoint("test_written", state)

    if state["phase"] == "implement":
        impl = await engine.spawn("implementer", alias="tdd-impl")
        try:
            await engine.send(
                impl,
                f"Now implement the feature: {feature}\n"
                f"Make the test at {test_path} pass.")
            await engine.bash_predicate(
                f"uv run pytest {test_path}",
                retry_with=("Tests are still failing. "
                            "Output:\n{stdout}\n{stderr}"),
                max_retries=3)
        finally:
            await engine.close(impl)
        state = {"phase": "review"}
        await engine.checkpoint("implemented", state)

    if state["phase"] == "review":
        reviewer = await engine.spawn("reviewer", alias="tdd-reviewer")
        try:
            review = await engine.send(
                reviewer,
                f"Final review of {feature} and its test at {test_path}.")
            engine.log(f"Review:\n{review}")
        finally:
            await engine.close(reviewer)
        state = {"phase": "done"}
        await engine.checkpoint("reviewed", state)

    return f"tdd_cycle complete for {feature}"
