"""Persona system-prompt resolution.

An agent's optional `prompt:` field points at a Markdown file — its
persona. `read_persona` resolves + reads it at spawn; drivers inject the
returned text as a system prompt that composes with (never replaces) the
aegis handle/callback primer.
"""
from __future__ import annotations

from pathlib import Path

from aegis.config import Agent, ConfigError


def read_persona(agent: Agent, cwd: str) -> str | None:
    """Read the persona file referenced by `agent.prompt`.

    The path is `~`-expanded; a relative path is resolved under `cwd`.
    Returns `None` when `agent.prompt` is unset. Raises `ConfigError`
    when the path is set but unreadable.
    """
    rel = getattr(agent, "prompt", None)
    if not rel:
        return None
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(
            f"persona prompt file {p} is unreadable: {e}") from e
