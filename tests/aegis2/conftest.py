import asyncio
import stat
import sys
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def fake_claude(tmp_path: Path) -> str:
    """An executable that runs the fake claude, as a session would exec it."""
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    home = tmp_path / "fake-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CLAUDE_HOME="{home}"\nexec "{sys.executable}" "{FAKE}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


async def until(pred, timeout: float = 3.0, what: str = "condition") -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)
