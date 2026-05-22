"""OpenCode driver — ACP-based.

Replaces the v1 one-shot driver. Multi-turn per session, per-session
aegis-MCP injection. All protocol heavy-lifting is in ``acp.py``.
"""
from __future__ import annotations

from aegis.drivers.acp import AcpDriver


class OpenCodeDriver(AcpDriver):
    BASE_CMD = ["opencode", "acp"]
    supports_resume = False
