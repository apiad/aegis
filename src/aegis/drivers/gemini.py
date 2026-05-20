"""Gemini CLI driver — ACP-based.

Replaces the v1 one-shot driver. Multi-turn per session, per-session
aegis-MCP injection, OAuth pass-through (cached creds at
``~/.gemini/oauth_creds.json`` are unaffected). Gemini's ``--acp``
subcommand accepts ``-m <model>`` for model selection; OpenCode's
``opencode acp`` does NOT (model is set in its own config), so the
``-m`` injection lives here rather than in the generic AcpDriver.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class GeminiDriver(AcpDriver):
    BASE_CMD = ["gemini", "--acp"]

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        argv = list(self.BASE_CMD)
        if getattr(agent, "model", ""):
            argv += ["-m", agent.model]
        return argv
