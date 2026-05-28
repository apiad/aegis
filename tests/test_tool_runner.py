"""Tool invocation: timeout, exception handling, JSONL logging."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aegis.tools.decorator import ToolEntry
from aegis.tools.runner import ToolTimeout, invoke_tool


@pytest.mark.asyncio
async def test_invoke_returns_result(tmp_path: Path) -> None:
    async def my_tool(x: int) -> str: return f"got {x}"
    entry = ToolEntry(name="my_tool", func=my_tool, timeout=5.0, qualname="t.my")
    out = await invoke_tool(entry, kwargs={"x": 7}, state_dir=tmp_path)
    assert out == "got 7"
    log = tmp_path / "tools" / "my_tool.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "ok"


@pytest.mark.asyncio
async def test_timeout_raises_typed_error(tmp_path: Path) -> None:
    async def slow() -> str:
        await asyncio.sleep(10); return "no"
    entry = ToolEntry(name="slow", func=slow, timeout=0.05, qualname="t.slow")
    with pytest.raises(ToolTimeout):
        await invoke_tool(entry, kwargs={}, state_dir=tmp_path)
    log = tmp_path / "tools" / "slow.jsonl"
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["status"] == "timeout"


@pytest.mark.asyncio
async def test_sync_function_invoked(tmp_path: Path) -> None:
    def plain() -> str: return "sync"
    entry = ToolEntry(name="plain", func=plain, timeout=5.0, qualname="t.plain")
    assert await invoke_tool(entry, kwargs={}, state_dir=tmp_path) == "sync"


@pytest.mark.asyncio
async def test_exception_logged_and_reraised(tmp_path: Path) -> None:
    async def boom() -> str: raise RuntimeError("nope")
    entry = ToolEntry(name="boom", func=boom, timeout=5.0, qualname="t.boom")
    with pytest.raises(RuntimeError, match="nope"):
        await invoke_tool(entry, kwargs={}, state_dir=tmp_path)
    rec = json.loads(
        (tmp_path / "tools" / "boom.jsonl").read_text().strip().splitlines()[-1]
    )
    assert rec["status"] == "exception"
