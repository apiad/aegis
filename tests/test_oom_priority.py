"""Each harness child raises its own OOM score, so in a host OOM the kernel
kills a session's tree before the server (#360)."""

import sys
from pathlib import Path

import pytest

from aegis.claude.process import ClaudeProcess, build_argv
from aegis.codex.process import CodexProcess
from aegis.harness import OOM_SCORE_ADJ, Launch, raise_oom_score
from aegis.opencode.process import OpenCodeProcess

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="oom_score_adj is a Linux /proc file"
)


def score(pid: int) -> int:
    return int(Path(f"/proc/{pid}/oom_score_adj").read_text())


def launch(tmp_path: Path) -> Launch:
    return Launch(
        cwd=tmp_path,
        model="fake",
        effort="high",
        permission="full",
        resume_id=None,
        mcp=None,
        system_prompt=None,
        stderr_path=tmp_path / "stderr.log",
        on_line=lambda line: None,
        on_exit=lambda code, tail: None,
    )


async def test_a_claude_child_raises_its_oom_score(tmp_path, fake_claude):
    p = ClaudeProcess(
        build_argv(fake_claude, "opus", "high", "full"),
        tmp_path,
        tmp_path / "err.log",
        lambda line: None,
        lambda code, tail: None,
    )
    await p.start()
    try:
        assert p.pid is not None
        assert score(p.pid) == OOM_SCORE_ADJ
    finally:
        await p.terminate()


async def test_an_opencode_child_raises_its_oom_score(tmp_path, fake_opencode):
    p = OpenCodeProcess(fake_opencode, launch(tmp_path))
    await p.start()
    try:
        assert p.pid is not None
        assert score(p.pid) == OOM_SCORE_ADJ
    finally:
        await p.terminate()


async def test_a_codex_child_raises_its_oom_score(tmp_path, fake_codex):
    p = CodexProcess(fake_codex, launch(tmp_path))
    await p.start()
    try:
        assert p.pid is not None
        assert score(p.pid) == OOM_SCORE_ADJ
    finally:
        await p.terminate()


def test_a_process_that_is_gone_is_skipped_quietly():
    raise_oom_score(2**22 + 1)  # above pid_max's ceiling: no such /proc entry
