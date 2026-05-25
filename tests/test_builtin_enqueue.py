"""Tests for the built-in ``enqueue`` workflow."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aegis.workflows.builtins.enqueue import enqueue


@pytest.mark.asyncio
async def test_enqueue_fire_and_forget() -> None:
    engine = AsyncMock()
    engine.enqueue.return_value = "task-id-123"
    result = await enqueue(engine, queue="tasks", payload="do thing",
                           callback=False)
    assert result == "task-id-123"
    engine.enqueue.assert_awaited_with(
        "tasks", "do thing", callback=False)


@pytest.mark.asyncio
async def test_enqueue_callback_awaits_result() -> None:
    engine = AsyncMock()
    engine.enqueue.return_value = "worker said hi"
    result = await enqueue(engine, queue="tasks", payload="do thing",
                           callback=True)
    assert result == "worker said hi"
    engine.enqueue.assert_awaited_with(
        "tasks", "do thing", callback=True)


def test_enqueue_is_registered() -> None:
    from aegis.workflow import get_workflow
    assert get_workflow("enqueue") is not None
