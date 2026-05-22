"""brainstorm_to_spec — host interviews user, drafts a spec, writes it.

Stub for slice 5 scaffolding. Fleshed out in slice 6.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow("brainstorm_to_spec")
async def brainstorm_to_spec(engine, *, topic: str | None = None) -> str:
    return "stub"
