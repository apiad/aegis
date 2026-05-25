"""Tests for the YAML config loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def test_load_inline_only(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: claude
        agents:
          claude:
            provider: claude-code
            model: opus
            effort: high
            permission: auto
        queues:
          tasks:
            agent: claude
            max_parallel: 2
    """))
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "claude"
    assert "claude" in cfg.agents
    assert cfg.agents["claude"].provider.model == "opus"
    assert cfg.queues["tasks"].max_parallel == 2


def test_load_empty_yaml(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    cfg = load_config(tmp_path)
    assert cfg.agents == {}
    assert cfg.queues == {}
    assert cfg.schedules == {}


def test_unknown_provider_fails(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        agents:
          x:
            provider: bogus
            model: nope
    """))
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(tmp_path)


def test_missing_provider_fails(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        agents:
          x:
            model: nope
    """))
    with pytest.raises(ConfigError, match="missing `provider`"):
        load_config(tmp_path)


def test_schedules_inline(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        schedules:
          eod:
            workflow: prompt
            args: {agent: claude, text: hi}
            cron: "0 2 * * *"
            timezone: UTC
            lifecycle: forever
    """))
    cfg = load_config(tmp_path)
    assert "eod" in cfg.schedules
    assert cfg.schedules["eod"]["cron"] == "0 2 * * *"
    assert cfg.schedules["eod"]["args"]["agent"] == "claude"


def test_scheduler_top_level(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        scheduler:
          tick_seconds: 5
          default_timezone: America/Havana
    """))
    cfg = load_config(tmp_path)
    assert cfg.scheduler["tick_seconds"] == 5
    assert cfg.scheduler["default_timezone"] == "America/Havana"


def test_workflows_list(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("workflows: [prompt, enqueue]\n")
    cfg = load_config(tmp_path)
    assert cfg.workflows == ["prompt", "enqueue"]


def test_yaml_missing_file_returns_empty(tmp_path: Path) -> None:
    """Loader is tolerant of a missing .aegis.yaml at root — returns
    an empty AegisConfig. Caller decides whether to fail."""
    cfg = load_config(tmp_path)
    assert cfg.agents == {}
