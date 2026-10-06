"""The one-shot must not load the project's instructions.

Measured 2026-08-26 on zion: a 16-char prompt costs 20,611 input tokens at
cwd=Workspace and 6,319 in an empty dir. The difference is CLAUDE.md and
skills — which a generation call cannot use, because it is handed its
window explicitly.
"""
import json

from pydantic import BaseModel

from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver


class _Schema(BaseModel):
    line: str


def _argv():
    d = ClaudeDriver()
    agent = Agent(harness="claude-code", model="haiku")
    return d._oneshot_argv(agent, _Schema, ["hello"])


def test_oneshot_loads_no_setting_sources():
    argv = _argv()
    assert "--setting-sources" in argv
    # The value must be empty: user/project/local are exactly the three
    # sources that pull in CLAUDE.md, skills and plugins.
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_oneshot_still_sheds_tools_and_mcp():
    """Regression guard: the new flag must not displace the old ones."""
    argv = _argv()
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {
        "mcpServers": {}}


def test_oneshot_does_not_use_exclude_dynamic():
    """Measured: it moves tokens without removing any (21,445 either way)."""
    argv = _argv()
    assert "--exclude-dynamic-system-prompt-sections" not in argv
