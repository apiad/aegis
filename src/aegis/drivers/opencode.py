"""OpenCode driver — ACP-based.

Replaces the v1 one-shot driver. Multi-turn per session, per-session
aegis-MCP injection. All protocol heavy-lifting is in ``acp.py``.
"""
from __future__ import annotations

import json

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class OpenCodeDriver(AcpDriver):
    BASE_CMD = ["opencode", "acp"]

    def extra_env(self, agent: Agent) -> dict[str, str]:
        model = getattr(agent, "model", "") or ""
        if not model:
            return {}
        # opencode acp has no -m flag; it reads its model from config.
        # OPENCODE_CONFIG_CONTENT is inline JSON merged over the discovered
        # config, so the repo opencode.json MCP block is preserved.
        return {"OPENCODE_CONFIG_CONTENT": json.dumps({"model": model})}
