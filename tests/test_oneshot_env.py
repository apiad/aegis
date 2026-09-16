"""One-shot generation must not pay for reasoning it does not use.

Measured 2026-09-16 on a recap-shaped call (haiku 4.5, CLI 2.1.270):
thinking on took 27.1s and 2,508 output tokens to write two sentences,
and its latency swung 8.7s-30.4s run to run; off took 4.7s and 103
tokens, every time. `--effort` has no off switch — `low` still emitted
133 thinking tokens — so the environment variable is the only real one.
"""
import asyncio

import pytest
from pydantic import BaseModel

from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver


class _Two(BaseModel):
    line: str


@pytest.fixture
def agent():
    return Agent(harness="claude-code", model="claude-haiku-4-5-20251001")


def test_generate_passes_thinking_budget_zero(monkeypatch, agent, tmp_path):
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["env"] = kw.get("env")
        raise RuntimeError("stop here — we only care about the env")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi"))

    assert seen["env"] is not None, "generate_detailed inherited the daemon env"
    assert seen["env"]["MAX_THINKING_TOKENS"] == "0"


def test_generate_env_keeps_the_rest_of_the_environment(monkeypatch, agent, tmp_path):
    """Replacing os.environ wholesale would drop PATH and the API key."""
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["env"] = kw.get("env")
        raise RuntimeError("stop")

    monkeypatch.setenv("AEGIS_PROBE_MARKER", "present")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi"))

    assert seen["env"]["AEGIS_PROBE_MARKER"] == "present"


def test_generate_runs_from_a_neutral_directory_not_the_project(monkeypatch, agent, tmp_path):
    """A generation call has no tools and is handed its window, so the cwd
    buys nothing and costs a lot. Measured 2026-09-16 on a real in-flight
    recap, same argv: 11,445 input tokens and $0.0287 launched from the
    Workspace root against 4,902 and $0.0162 from /tmp, despite
    `--setting-sources ""`."""
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["cwd"] = kw.get("cwd")
        raise RuntimeError("stop")

    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("a large project instruction file\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(project), _Two, "hi"))

    from pathlib import Path

    cwd = Path(seen["cwd"])
    assert cwd != project
    assert cwd.is_dir()
    assert list(cwd.iterdir()) == [], "the neutral directory must stay empty"


async def test_cancelling_a_generation_kills_its_claude_process(monkeypatch, agent, tmp_path):
    """A cancelled one-shot must not leave `claude -p` running and billing.

    The turn recap is cancelled when a newer one supersedes it, the fleet
    recap when its turn ends or its last watcher leaves. Before the fix the
    child ran to completion after the task was gone, unseen and paid for.
    """
    import os
    import stat

    bindir = tmp_path / "bin"
    bindir.mkdir()
    started, finished = tmp_path / "started", tmp_path / "finished"
    fake = bindir / "claude"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo $$ > {started}\n"
        "sleep 3\n"
        f"echo done > {finished}\n"
        "echo '{}'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(
        ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi")
    )
    for _ in range(200):
        if started.exists() and started.read_text().strip():
            break
        await asyncio.sleep(0.01)
    pid = int(started.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    def alive() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    for _ in range(100):
        if not alive():
            break
        await asyncio.sleep(0.01)
    assert not alive(), "the claude process outlived its cancelled call"
    await asyncio.sleep(3.5)
    assert not finished.exists(), "the cancelled call ran to completion"


def test_a_call_that_asks_to_think_keeps_the_clis_thinking_default(monkeypatch, agent, tmp_path):
    """The loop judge decides whether a turn satisfied an instruction, the one
    one-shot where reasoning may earn its cost, and the spec excludes it from
    the thinking cut until it is measured. `think=True` leaves the CLI's own
    default: MAX_THINKING_TOKENS is not forced to 0."""
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["env"] = kw.get("env")
        raise RuntimeError("stop")

    monkeypatch.delenv("MAX_THINKING_TOKENS", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(ClaudeDriver().generate_detailed(agent, str(tmp_path), _Two, "hi", think=True))
    assert "MAX_THINKING_TOKENS" not in seen["env"]


def test_the_loop_judge_asks_to_think(monkeypatch, agent, tmp_path):
    """Wired at the call site, driven through the real judge_for."""
    from aegis.core.loop_judge import judge_for
    from aegis.digest.models import TurnFacts
    from aegis.drivers.oneshot import Generation

    kwargs = {}

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, agent, cwd, schema, *instructions, **kw):
            kwargs.update(kw)
            return Generation()

    replay = type("R", (), {"events": [], "stamps": []})()
    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    monkeypatch.setattr("aegis.state.session_log.replay_events", lambda *a: replay)
    asyncio.run(judge_for(
        state_dir=tmp_path, log_id="x", instruction="do it", iteration=1,
        max_iterations=20, facts=TurnFacts(), still_streak=0, advisory="",
        agent=agent, agents={"a": agent}, cwd=str(tmp_path), root=tmp_path,
    ))
    assert kwargs.get("think") is True
