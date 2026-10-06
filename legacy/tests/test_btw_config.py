"""The `text_generation:` config key — which profile pays for a side note.

The knob does real work: measured on zion 2026-07-31, the same one-shot
costs $0.0044 on haiku. Left pointing at an Opus profile it is roughly
15x that, for a question the operator fired without thinking.
"""
from __future__ import annotations

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config

BASE = """
agents:
  opus:
    provider: claude-code
    model: opus
  haiku:
    provider: claude-code
    model: haiku
default_agent: opus
"""


def write(tmp_path, body: str):
    (tmp_path / ".aegis.yaml").write_text(body)
    return load_config(tmp_path)


def test_text_generation_defaults_to_unset(tmp_path):
    """Unset is meaningful: /btw falls back to the session's own profile
    and warns, rather than silently picking a model for you."""
    assert write(tmp_path, BASE).text_generation is None


def test_text_generation_names_an_agent_profile(tmp_path):
    cfg = write(tmp_path, BASE + "text_generation: haiku\n")
    assert cfg.text_generation == "haiku"


def test_text_generation_must_reference_a_declared_agent(tmp_path):
    """Fail loud at boot, like `default_agent` and queue agents — not at
    the moment someone fires a side note."""
    with pytest.raises(ConfigError) as e:
        write(tmp_path, BASE + "text_generation: nonesuch\n")
    assert "text_generation" in str(e.value)
    assert "nonesuch" in str(e.value)
