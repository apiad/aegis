"""review_branch — spawn a reviewer agent to critique the working diff.

Stub for slice 5 scaffolding. Fleshed out in slice 8.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow("review_branch")
async def review_branch(engine, *, base: str = "main") -> str:
    return "stub"
