"""AcpSession.start() honors registered pre_spawn hooks.

These tests assert the hook is consulted before the ACP subprocess is
spawned. A blocking hook prevents the subprocess from being launched at
all (no need for a real gemini/opencode binary on PATH).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import Agent, GeminiCLI
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver
from aegis.hooks import PreSpawnResult, hook
from aegis.hooks.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_gemini_pre_spawn_block_prevents_subprocess(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    @hook("pre_spawn")
    async def deny(ctx):
        seen["argv"] = ctx.argv
        seen["harness"] = ctx.session.harness
        return PreSpawnResult(block="denied for test")

    drv = GeminiDriver()
    sess = drv.session(
        Agent(provider=GeminiCLI(model="gemini-2.5-pro")),
        str(tmp_path), "http://nowhere", "handle-1",
    )
    with pytest.raises(RuntimeError, match="denied for test"):
        await sess.start()
    assert sess._proc is None
    assert seen["argv"][0] == "gemini"
    assert seen["harness"] == "gemini"


@pytest.mark.asyncio
async def test_opencode_pre_spawn_block_prevents_subprocess(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    @hook("pre_spawn")
    async def deny(ctx):
        seen["argv"] = ctx.argv
        seen["harness"] = ctx.session.harness
        return PreSpawnResult(block="denied for test")

    drv = OpenCodeDriver()
    sess = drv.session(
        Agent(harness="opencode", model="anthropic/claude-sonnet-4-5"),
        str(tmp_path), "http://nowhere", "handle-2",
    )
    with pytest.raises(RuntimeError, match="denied for test"):
        await sess.start()
    assert sess._proc is None
    assert seen["argv"][0] == "opencode"
    assert seen["harness"] == "opencode"
