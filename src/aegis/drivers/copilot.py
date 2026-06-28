"""GitHub Copilot CLI driver, ACP-based.

Copilot CLI exposes an Agent Client Protocol server over stdio
(``copilot --acp``), the same protocol that backs the Gemini and
OpenCode drivers, so all the protocol heavy-lifting stays in ``acp.py``.
Model selection rides the global ``--model`` flag (Gemini uses ``-m``;
OpenCode reads it from its own config), so the injection lives here
rather than in the generic AcpDriver. Auth goes through the existing
``copilot``/``gh`` login, with no separate token management.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class CopilotDriver(AcpDriver):
    BASE_CMD = ["copilot", "--acp"]

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        argv = list(self.BASE_CMD)
        if getattr(agent, "model", ""):
            argv += ["--model", agent.model]
        return argv
