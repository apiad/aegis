"""Claude Code's own tools that aegis replaces are off in a session with
aegis's tools, unless its agent keeps them (#461)."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from aegis.agents import load_agents, resolve
from aegis.claude.harness import ClaudeSession
from aegis.claude.process import REPLACED_TOOLS, build_argv
from aegis.harness import Launch
from aegis.session import SpawnSpec

HAIKU = "claude-haiku-4-5-20251001"


def launch(tmp_path: Path, mcp, keep=(), on_line=lambda line: None) -> Launch:
    return Launch(
        cwd=tmp_path,
        model=HAIKU,
        effort="low",
        permission="full",
        resume_id=None,
        mcp=mcp,
        system_prompt=None,
        stderr_path=tmp_path / "stderr.log",
        on_line=on_line,
        on_exit=lambda code, tail: None,
        keep_harness_tools=keep,
    )


def disallowed(argv: list[str]) -> list[str]:
    if "--disallowedTools" not in argv:
        return []
    return argv[argv.index("--disallowedTools") + 1].split(",")


def test_build_argv_joins_the_disallowed_tools_into_one_value():
    argv = build_argv("claude", "opus", "high", "full", disallowed=("Agent", "Monitor"))
    assert disallowed(argv) == ["Agent", "Monitor"]
    assert "--disallowedTools" not in build_argv("claude", "opus", "high", "full")


def test_a_session_with_aegis_tools_runs_without_the_ones_they_replace(tmp_path):
    s = ClaudeSession("claude", launch(tmp_path, ("http://127.0.0.1:1/mcp", "t")))
    assert disallowed(s._proc.argv) == list(REPLACED_TOOLS)


def test_a_session_without_aegis_tools_keeps_every_harness_tool(tmp_path):
    s = ClaudeSession("claude", launch(tmp_path, None))
    assert disallowed(s._proc.argv) == []


def test_an_agent_keeps_the_tools_it_names(tmp_path):
    s = ClaudeSession(
        "claude",
        launch(tmp_path, ("http://127.0.0.1:1/mcp", "t"), keep=("Agent", "Monitor")),
    )
    off = disallowed(s._proc.argv)
    assert "Agent" not in off and "Monitor" not in off
    assert set(off) == set(REPLACED_TOOLS) - {"Agent", "Monitor"}


def test_keep_harness_tools_reaches_the_spec_and_survives_its_record(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  fan: {harness: claude-code, model: opus, effort: high, permission: full,"
        " keep_harness_tools: [Agent]}\n"
    )
    spec = resolve(load_agents(tmp_path), None, "fan", {}, tmp_path)
    assert spec.keep_harness_tools == ("Agent",)
    again = SpawnSpec.from_record(spec.record(), tmp_path)
    assert again.keep_harness_tools == ("Agent",)
    plain = SpawnSpec("opus", "opus", "high", "full", tmp_path)
    assert "keep_harness_tools" not in plain.record()


@pytest.mark.parametrize(
    "value, error",
    [
        ("[Agnet]", "keep_harness_tools names Agnet, which aegis does not turn off"),
        ("Agent", "keep_harness_tools must be a list of tool names"),
    ],
)
def test_a_wrong_keep_harness_tools_disables_the_agent(tmp_path, value, error):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  fan: {harness: claude-code, model: opus, effort: high, permission: full,"
        f" keep_harness_tools: {value}}}\n"
    )
    (a,) = load_agents(tmp_path)
    assert not a.enabled and a.error.startswith(error)


@pytest.mark.live
async def test_real_claude_starts_without_the_replaced_tools_but_the_kept_one(
    tmp_path: Path,
):
    """The real binary's init line lists the tools it loaded: none of the
    replaced ones but the kept Agent. An MCP endpoint that does not answer is
    enough to switch the deny on; Claude starts without it."""
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    lines: list[dict] = []
    s = ClaudeSession(
        claude,
        launch(
            tmp_path,
            ("http://127.0.0.1:1/mcp", "t"),
            keep=("Agent",),
            on_line=lambda line: lines.append(json.loads(line)),
        ),
    )
    await s.start()
    try:
        await s.send("Reply with the single word OK.")
        for _ in range(900):
            if any(
                d.get("type") == "system" and d.get("subtype") == "init" for d in lines
            ):
                break
            await asyncio.sleep(0.1)
    finally:
        await s.terminate()
    (init,) = [
        d for d in lines if d.get("type") == "system" and d.get("subtype") == "init"
    ]
    # The init line names Agent by its older name.
    tools = {"Agent" if t == "Task" else t for t in init["tools"]}
    assert "Bash" in tools and "Agent" in tools
    assert not tools & (set(REPLACED_TOOLS) - {"Agent"})
