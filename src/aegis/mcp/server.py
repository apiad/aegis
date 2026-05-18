from __future__ import annotations

import json

from fastmcp import FastMCP

BRIEFING = (
    "You are running inside aegis — a meta-harness for coding agents. "
    "aegis drives this Claude Code process via stream-json and re-renders "
    "it in a multi-agent terminal UI; you are one agent inside it.\n\n"
    "You are connected to the aegis MCP server. Because aegis runs with "
    "strict MCP config, this is your ONLY MCP server — other MCP servers "
    "from the user's config are not loaded in aegis sessions.\n\n"
    "aegis tools available to you now:\n"
    "  - aegis_meta() : this briefing.\n\n"
    "More aegis tools (vault/file/web/workflow) are planned. Built-in "
    "Claude tools (Read, Edit, Bash, WebFetch, …) are unchanged and "
    "available. Call aegis_meta once at the start to orient, then proceed "
    "with the user's request. When the user asks what you can do, "
    "summarise this briefing."
)

PRIMING = (
    "You are running inside aegis, a meta-harness. An MCP server named "
    "'aegis' is attached and (strict config) is your only MCP server. "
    "Call its aegis_meta tool first to learn this environment and the "
    "aegis tools available to you, then proceed."
)


def aegis_meta() -> str:
    """Orientation briefing: where you are and what aegis offers."""
    return BRIEFING


def build_server() -> FastMCP:
    server = FastMCP("aegis")
    server.tool(aegis_meta)
    return server


def mcp_config_json(url: str) -> str:
    return json.dumps(
        {"mcpServers": {"aegis": {"type": "http", "url": url}}})
