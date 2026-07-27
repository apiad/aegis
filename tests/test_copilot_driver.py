"""CopilotDriver argv construction.

Copilot is an ACP driver (``copilot --acp``) like Gemini and OpenCode;
the only provider-specific flag is global ``--model`` passthrough.
"""
from aegis.config import Agent
from aegis.drivers import DRIVERS
from aegis.drivers.copilot import CopilotDriver

MCP_URL = "http://127.0.0.1:9/mcp/"
HANDLE = "lucid-knuth"


def argv_for(model="claude-sonnet-4.5"):
    agent = Agent(harness="copilot", model=model, permission="full")
    return CopilotDriver().build_argv(agent, "/tmp/wd", MCP_URL, HANDLE)


def test_registry_has_copilot():
    assert DRIVERS["copilot"] is CopilotDriver


def test_base_cmd_is_copilot_acp():
    argv = argv_for()
    assert argv[0] == "copilot"
    assert "--acp" in argv


def test_model_passthrough():
    argv = argv_for(model="gpt-5.4")
    assert argv[argv.index("--model") + 1] == "gpt-5.4"


def test_no_model_emits_no_model_flag():
    argv = argv_for(model="")
    assert "--model" not in argv
    assert argv == ["copilot", "--acp"]
