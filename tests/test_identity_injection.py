from __future__ import annotations

import json

from aegis.hosts.launcher import _substitute_mcp_url
from aegis.mcp import mcp_config_json
from aegis.mcp.identity import HEADER


def _headers(blob: str) -> dict:
    return json.loads(blob)["mcpServers"]["aegis"].get("headers", {})


def test_config_without_a_token_carries_no_headers():
    assert _headers(mcp_config_json("http://x/mcp/")) == {}


def test_config_with_a_token_carries_the_header():
    blob = mcp_config_json("http://x/mcp/", "tok123")
    assert _headers(blob) == {"X-Aegis-Session": "tok123"}


def test_the_header_name_matches_what_the_server_looks_for():
    blob = mcp_config_json("http://x/mcp/", "tok123")
    assert list(_headers(blob))[0].lower() == HEADER


def test_substitute_fills_a_tokenful_placeholder_and_keeps_the_header():
    """The trap this feature would otherwise have walked into.

    The launcher used to rewrite the argv element that compared EQUAL to a
    reconstructed `mcp_config_json("")`. Baking a token into the blob made
    the two strings differ, so nothing matched, nothing was substituted,
    and every SSH-hosted session came up with an empty MCP URL — no aegis
    plane at all, silently. Matching on the config's shape means the
    launcher never has to know the token, and the header survives.
    """
    argv = ["claude", "--mcp-config", mcp_config_json("", "tok123"), "-p"]
    out = _substitute_mcp_url(argv, "http://real/mcp/")
    assert json.loads(out[2])["mcpServers"]["aegis"]["url"] == "http://real/mcp/"
    assert _headers(out[2]) == {"X-Aegis-Session": "tok123"}


def test_substitute_still_works_with_no_token():
    argv = ["claude", "--mcp-config", mcp_config_json(""), "-p"]
    out = _substitute_mcp_url(argv, "http://real/mcp/")
    assert json.loads(out[2])["mcpServers"]["aegis"]["url"] == "http://real/mcp/"


def test_substitute_leaves_unrelated_arguments_alone():
    argv = ["claude", "-p", "--model", "opus", mcp_config_json("", "t")]
    out = _substitute_mcp_url(argv, "http://real/mcp/")
    assert out[:4] == ["claude", "-p", "--model", "opus"]


def test_substitute_leaves_a_config_that_already_has_a_url_alone():
    """A local session's URL is real from the start; only the deferred
    (empty) placeholder is the launcher's business."""
    argv = ["claude", "--mcp-config", mcp_config_json("http://local/mcp/")]
    out = _substitute_mcp_url(argv, "http://real/mcp/")
    assert json.loads(out[2])["mcpServers"]["aegis"]["url"] == "http://local/mcp/"


def test_substitute_ignores_a_foreign_mcp_config():
    """Someone else's MCP server is not ours to rewrite."""
    foreign = json.dumps({"mcpServers": {"other": {"type": "http", "url": ""}}})
    out = _substitute_mcp_url(["claude", foreign], "http://real/mcp/")
    assert out[1] == foreign


def test_substitute_survives_a_non_json_argument():
    argv = ["claude", "--append-system-prompt", "talk about mcpServers"]
    assert _substitute_mcp_url(argv, "http://real/mcp/") == argv


def test_build_argv_bakes_the_token_into_the_config():
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver
    agent = Agent(harness="claude-code", model="opus", effort="medium",
                  permission="auto")
    argv = ClaudeDriver().build_argv(agent, ".", "http://x/mcp/", "alice",
                                     token="tok123")
    blob = argv[argv.index("--mcp-config") + 1]
    assert _headers(blob) == {"X-Aegis-Session": "tok123"}
