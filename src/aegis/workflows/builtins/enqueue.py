"""Built-in ``enqueue`` workflow.

Wrap ``aegis_enqueue`` (the queue substrate's MCP tool) as a workflow
so a schedule fire can compose with a queue's ``max_parallel`` cap.

- ``callback=False`` (default): fire-and-forget. Returns the queued
  task's id; the worker runs asynchronously.
- ``callback=True``: await the worker's callback and return its final
  assistant text.
"""
from __future__ import annotations

from aegis.workflow import workflow


@workflow
async def enqueue(engine, *, queue: str, payload: str,
                  callback: bool = False) -> str:
    """Drop ``payload`` on ``queue``; return task_id (or worker text
    if ``callback=True``)."""
    return await engine.enqueue(queue, payload, callback=callback)
