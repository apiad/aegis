"""Lovelaice driver — native harness-free agent over official ACP v1.

Spawns ``lovelaice-acp`` (lovelaice's ACP-v1 stdio server) and drives it
with the generic ``AcpDriver``. Model / endpoint / key are injected as env
at spawn (lovelaice reads ``LOVELAICE_MODEL`` / ``LOVELAICE_BASE_URL`` /
``OPENROUTER_API_KEY``). Point ``base_url`` at a local endpoint for local
models; set ``api_key_file`` for a direct-API key.

Spec: ``docs/superpowers/specs/2026-07-10-lovelaice-native-acp-agent-design.md``
"""
from __future__ import annotations

from pathlib import Path

from aegis.config import Agent
from aegis.drivers.acp import AcpDriver


class LovelaiceDriver(AcpDriver):
    BASE_CMD = ["lovelaice-acp"]

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        return list(self.BASE_CMD)

    def extra_env(self, agent: Agent) -> dict[str, str]:
        env: dict[str, str] = {}
        if getattr(agent, "model", ""):
            env["LOVELAICE_MODEL"] = agent.model
        provider = getattr(agent, "provider", None)
        base_url = getattr(provider, "base_url", None)
        if base_url:
            env["LOVELAICE_BASE_URL"] = base_url
        key_file = getattr(provider, "api_key_file", None)
        if key_file:
            p = Path(key_file).expanduser()
            if p.is_file():
                env["OPENROUTER_API_KEY"] = p.read_text().strip()
        return env
