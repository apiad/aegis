"""tdd_cycle — write failing tests, implement, iterate until green.

Stub for slice 5 scaffolding. Fleshed out in slice 9.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow("tdd_cycle")
async def tdd_cycle(engine, *, plan_step: str | None = None,
                    test_command: str = "uv run pytest",
                    test_path: str = "tests/test_step.py") -> str:
    return "stub"
