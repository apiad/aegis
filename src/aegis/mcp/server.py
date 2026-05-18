from __future__ import annotations

import dataclasses
import json

from fastmcp import FastMCP

from aegis.mcp.bridge import AppBridge

BRIEFING = (
    "You are running inside aegis — a meta-harness for coding agents. "
    "aegis drives this Claude Code process via stream-json and re-renders "
    "it in a multi-agent terminal UI; you are one agent inside it.\n\n"
    "You are connected to the aegis MCP server. Because aegis runs with "
    "strict MCP config, this is your ONLY MCP server — other MCP servers "
    "from the user's config are not loaded in aegis sessions.\n\n"
    "aegis tools available to you now:\n"
    "  - aegis_meta() : this briefing.\n"
    "  - aegis_list_sessions() : the live aegis sessions (your peers). "
    "Each entry has handle, agent_slug, state, active, unseen. Use this "
    "to see who you can hand off to and whether they are idle.\n"
    "  - aegis_list_agents() : the configured agent-profile slugs that "
    "could be spawned (spawn itself is a future tool, not in this "
    "release).\n"
    "  - aegis_handoff(from_handle, target_handle, context) : one-way "
    "(fire-and-forget) context transfer to a live peer session. You pass "
    "your own aegis handle as from_handle — it is in your system prompt. "
    "The target receives a tagged user turn and starts working; you do "
    "not wait for its reply. Returns 'delivered to <handle>' on success, "
    "or a 'handoff rejected: …' reason (self / unknown / busy).\n\n"
    "More aegis tools (vault/file/web/workflow) are planned. Built-in "
    "Claude tools (Read, Edit, Bash, WebFetch, …) are unchanged and "
    "available. Call aegis_meta once at the start to orient, then proceed "
    "with the user's request. When the user asks what you can do, "
    "summarise this briefing."
)

PRIMING = (
    "You are running inside aegis, a meta-harness. An MCP server named "
    "'aegis' is attached and (strict config) is your only MCP server. "
    "Your aegis handle is '{handle}'. Call its aegis_meta tool first to "
    "learn this environment and the aegis tools available to you, then "
    "proceed. When handing off to a peer, pass your handle '{handle}' "
    "as from_handle."
)


def aegis_meta() -> str:
    """Orientation briefing: where you are and what aegis offers."""
    return BRIEFING


def build_server(bridge: AppBridge) -> FastMCP:
    server = FastMCP("aegis")
    server.tool(aegis_meta)

    @server.tool
    async def aegis_list_sessions() -> list[dict]:
        """Live aegis sessions (peers you can hand off to)."""
        return [dataclasses.asdict(s) for s in bridge.list_sessions()]

    @server.tool
    async def aegis_list_agents() -> list[str]:
        """Configured agent profiles that could be spawned."""
        return list(bridge.list_agents())

    @server.tool
    async def aegis_handoff(from_handle: str, target_handle: str,
                            context: str) -> str:
        """One-way context transfer to a live peer aegis session.

        from_handle is your own aegis handle (read it from your system
        prompt). Returns 'delivered to <target>' on success, or a
        'handoff rejected: …' reason (self / unknown / busy).
        """
        return await bridge.handoff(from_handle, target_handle, context)

    return server


def mcp_config_json(url: str) -> str:
    return json.dumps(
        {"mcpServers": {"aegis": {"type": "http", "url": url}}})
