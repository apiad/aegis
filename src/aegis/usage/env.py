"""Project-rooted lookups for usage aggregation. Shared by the CLI
(``aegis usage``) and the ``/usage`` slash command so both see the same
state dir and default-agent model. No Textual — safe on the web path.

The root is a required argument, not resolved here: the CLI resolves it
from the invocation, the slash command takes it from the bridge's roots.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def state_dir(root: Path) -> Path:
    return Path(root) / ".aegis" / "state"


def default_agent(root: Path) -> tuple[str, str]:
    """(model, provider) of the config's default_agent, for sessions whose
    logs predate ``SystemInit.model``. Falls back to opus / claude-code."""
    cfg: dict = {}
    p = Path(root) / ".aegis.yaml"
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    da = cfg.get("default_agent")
    agent = (cfg.get("agents") or {}).get(da, {}) if da else {}
    return agent.get("model", "opus"), agent.get("provider", "claude-code")
