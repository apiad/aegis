"""execute_plan — drive an implementer subagent through a plan markdown.

Stub for slice 5 scaffolding. Fleshed out in slice 7.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow("execute_plan")
async def execute_plan(engine, *, plan_path: str | None = None) -> str:
    return "stub"
