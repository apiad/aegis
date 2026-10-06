"""ClaudeSession.start() honors registered pre_spawn hooks.

These tests don't spawn the real `claude` binary; they register a
pre_spawn hook that replaces argv with /bin/sh so the subprocess
actually runs (and exits with a known code) under test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.drivers.claude import ClaudeSession
from aegis.hooks import PreSpawnResult, hook
from aegis.hooks.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_pre_spawn_hook_runs_before_exec(tmp_path: Path) -> None:
    called: dict[str, object] = {}

    @hook("pre_spawn")
    async def record(ctx):
        called["argv"] = ctx.argv
        called["harness"] = ctx.session.harness
        # Replace argv with a known-good no-op so the subprocess starts.
        return PreSpawnResult(
            argv=("/bin/sh", "-c", "exit 0"))

    sess = ClaudeSession(
        ["claude", "-p", "--never-actually-invoked"],
        cwd=str(tmp_path),
        handle="test-handle",
        harness="claude-code",
    )
    await sess.start()
    assert sess._proc is not None
    await sess._proc.wait()
    assert sess._proc.returncode == 0
    # Hook saw the original argv built by the driver, not the rewritten one.
    assert called["argv"][0] == "claude"
    assert called["harness"] == "claude-code"
    await sess.close()


@pytest.mark.asyncio
async def test_pre_spawn_hook_can_set_env(tmp_path: Path) -> None:
    out = tmp_path / "envprobe.txt"

    @hook("pre_spawn")
    async def setenv(ctx):
        return PreSpawnResult(
            argv=("/bin/sh", "-c", f'printf "%s" "$AEGIS_PROBE" > {out}'),
            env={**ctx.env, "AEGIS_PROBE": "hello-from-hook"},
        )

    sess = ClaudeSession(
        ["claude"], cwd=str(tmp_path),
        handle="h", harness="claude-code",
    )
    await sess.start()
    await sess._proc.wait()
    await sess.close()
    assert out.read_text() == "hello-from-hook"


@pytest.mark.asyncio
async def test_strict_block_raises_at_start(tmp_path: Path) -> None:
    @hook("pre_spawn")
    async def deny(ctx):
        return PreSpawnResult(block="nope")

    sess = ClaudeSession(
        ["claude"], cwd=str(tmp_path),
        handle="h", harness="claude-code",
    )
    with pytest.raises(RuntimeError, match="nope"):
        await sess.start()
    assert sess._proc is None
