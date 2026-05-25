"""Tests for the built-in ``prompt`` workflow."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aegis.workflows.builtins.prompt import prompt


@pytest.mark.asyncio
async def test_prompt_spawns_sends_closes() -> None:
    engine = AsyncMock()
    engine.spawn.return_value = "fake-handle"
    engine.send.return_value = "final assistant text"
    result = await prompt(engine, agent="claude", text="hi")
    assert result == "final assistant text"
    engine.spawn.assert_awaited_with("claude")
    engine.send.assert_awaited_with("fake-handle", "hi")
    engine.close.assert_awaited_with("fake-handle")


@pytest.mark.asyncio
async def test_prompt_closes_on_send_failure() -> None:
    """If send raises, close still runs (finally block)."""
    engine = AsyncMock()
    engine.spawn.return_value = "h"
    engine.send.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        await prompt(engine, agent="c", text="x")
    engine.close.assert_awaited_with("h")


def test_prompt_is_registered() -> None:
    from aegis.workflow import get_workflow
    assert get_workflow("prompt") is not None
