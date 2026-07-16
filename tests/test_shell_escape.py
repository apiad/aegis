"""run_shell_escape: run a `!command` and format its result for injection
into the agent as a user message."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.tui.shell_escape import run_shell_escape


@pytest.mark.asyncio
async def test_captures_stdout():
    body = await run_shell_escape("echo hello", Path.cwd())
    assert body.startswith("$ echo hello")
    assert "hello" in body
    assert "[exited" not in body          # zero exit → no note


@pytest.mark.asyncio
async def test_runs_in_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    body = await run_shell_escape("ls", tmp_path)
    assert "marker.txt" in body


@pytest.mark.asyncio
async def test_merges_stderr():
    body = await run_shell_escape("echo oops 1>&2", Path.cwd())
    assert "oops" in body


@pytest.mark.asyncio
async def test_nonzero_exit_noted():
    body = await run_shell_escape("exit 3", Path.cwd())
    assert "[exited 3]" in body


@pytest.mark.asyncio
async def test_timeout_kills():
    body = await run_shell_escape("sleep 5", Path.cwd(), timeout=0.2)
    assert "timed out" in body


@pytest.mark.asyncio
async def test_output_capped():
    body = await run_shell_escape("yes x | head -c 100000", Path.cwd())
    assert "truncated" in body
    assert len(body) < 60_000
