"""Project-root resolution for usage aggregation. Shared by the CLI
(``aegis usage``) and the ``/usage`` slash command so both see the same
state dir and default-agent model. No Textual — safe on the web path.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _root() -> Path:
    from aegis.config import find_project_root
    return find_project_root() or Path.cwd()


def state_dir() -> Path:
    return _root() / ".aegis" / "state"


def default_agent() -> tuple[str, str]:
    """(model, provider) of the config's default_agent, for sessions whose
    logs predate ``SystemInit.model``. Falls back to opus / claude-code."""
    cfg: dict = {}
    p = _root() / ".aegis.yaml"
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    da = cfg.get("default_agent")
    agent = (cfg.get("agents") or {}).get(da, {}) if da else {}
    return agent.get("model", "opus"), agent.get("provider", "claude-code")
