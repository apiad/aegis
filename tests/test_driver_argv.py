import json

import pytest
from aegis.config import Agent
from aegis.drivers import DRIVERS, get_driver
from aegis.drivers.claude import ClaudeDriver


MCP_URL = "http://127.0.0.1:9/mcp/"
HANDLE = "lucid-knuth"


def argv_for(permission, effort="high", model="opus", mcp_url=MCP_URL,
             handle=HANDLE):
    agent = Agent(harness="claude-code", model=model,
                  effort=effort, permission=permission)
    return ClaudeDriver().build_argv(agent, "/tmp/wd", mcp_url, handle)


def test_registry_has_claude():
    assert DRIVERS["claude-code"] is ClaudeDriver


def test_fixed_stream_flags_always_present():
    argv = argv_for("auto")
    assert argv[0] == "claude"
    assert "-p" in argv
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--replay-user-messages" in argv
    # claude requires --verbose with -p + stream-json output (real-world
    # finding from fixture capture).
    assert "--verbose" in argv


def test_permission_mapping():
    assert "plan" in argv_for("read")
    assert "acceptEdits" in argv_for("write")
    assert "bypassPermissions" in argv_for("full")
    assert "auto" in argv_for("auto")


def test_effort_and_model_passthrough():
    argv = argv_for("auto", effort="max", model="sonnet")
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_unknown_harness_raises():
    with pytest.raises(KeyError):
        get_driver("opencode")


def test_build_argv_injects_strict_mcp_and_priming():
    argv = argv_for("auto", mcp_url="http://127.0.0.1:9/mcp/")
    assert "--strict-mcp-config" in argv
    i = argv.index("--mcp-config")
    cfg = json.loads(argv[i + 1])
    assert cfg["mcpServers"]["aegis"]["url"] == "http://127.0.0.1:9/mcp/"
    assert cfg["mcpServers"]["aegis"]["type"] == "http"
    j = argv.index("--append-system-prompt")
    assert "aegis_meta" in argv[j + 1]
    assert "-p" in argv


def test_aegis_plane_is_allowlisted_in_every_mode():
    # The aegis MCP server is aegis's own, trusted by construction.
    # Without an allowlist its tools hit a permission prompt that
    # cannot be answered under `claude -p`, so the call just fails.
    for perm in ("read", "write", "full", "auto"):
        argv = argv_for(perm)
        assert argv[argv.index("--allowedTools") + 1] == "mcp__aegis"


def test_build_argv_bakes_handle_into_priming():
    argv = argv_for("auto", handle="lucid-knuth")
    j = argv.index("--append-system-prompt")
    prompt = argv[j + 1]
    assert "lucid-knuth" in prompt
    assert "{handle}" not in prompt          # template was formatted
