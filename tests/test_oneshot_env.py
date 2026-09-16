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
