"""Built-in ``prompt`` workflow.

Direct spawn → send → close. The simplest possible composition: one
agent, one prompt, one final assistant text. Uncapped (does not go
through any queue). Use this from a scheduler entry when you want a
fire to start an agent immediately and read its final answer.

For a capped variant (going through a queue's max_parallel cap),
use the ``enqueue`` built-in instead.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow
async def prompt(engine, *, agent: str, text: str) -> str:
    """Spawn an agent of the named profile, send ``text`` as the
    opening user-turn, return the agent's final assistant text.

    Auto-closes the spawned handle on exit (success or failure).
    """
    handle = await engine.spawn(agent)
    try:
        return await engine.send(handle, text)
    finally:
        await engine.close(handle)
