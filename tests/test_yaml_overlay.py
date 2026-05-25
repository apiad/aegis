"""Tests for drop-in overlay collection + fail-loud conflict."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def test_overlay_only_schedules(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  c:\n    provider: claude-code\n    model: opus\n")
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "end-of-day.yaml").write_text(textwrap.dedent("""
        workflow: prompt
        args: {agent: c, text: hello}
        cron: "0 2 * * *"
        lifecycle: forever
    """))
    cfg = load_config(tmp_path)
    assert "end-of-day" in cfg.schedules
    assert cfg.schedules["end-of-day"]["cron"] == "0 2 * * *"


def test_overlay_and_inline_disjoint(tmp_path: Path) -> None:
    """Inline + overlay merge when keys are disjoint."""
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        schedules:
          inline-one:
            workflow: prompt
            cron: "0 1 * * *"
    """))
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "overlay-one.yaml").write_text(
        "workflow: prompt\ncron: '0 2 * * *'\n")
    cfg = load_config(tmp_path)
    assert set(cfg.schedules) == {"inline-one", "overlay-one"}


def test_conflict_fails_loud(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        schedules:
          foo:
            workflow: prompt
            cron: "* * * * *"
    """))
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "foo.yaml").write_text(
        "workflow: prompt\ncron: '* * * * *'\n")
    with pytest.raises(ConfigError, match="schedules"):
        load_config(tmp_path)


def test_agent_overlay(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    overlay = tmp_path / ".aegis" / "agents"
    overlay.mkdir(parents=True)
    (overlay / "claude.yaml").write_text(textwrap.dedent("""
        provider: claude-code
        model: opus
        effort: high
    """))
    cfg = load_config(tmp_path)
    assert "claude" in cfg.agents
    assert cfg.agents["claude"].provider.model == "opus"


def test_queue_overlay(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    overlay = tmp_path / ".aegis" / "queues"
    overlay.mkdir(parents=True)
    (overlay / "tasks.yaml").write_text(
        "agent: claude\nmax_parallel: 3\n")
    cfg = load_config(tmp_path)
    assert cfg.queues["tasks"].max_parallel == 3


def test_non_mapping_overlay_fails(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "bad.yaml").write_text("- a\n- b\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(tmp_path)
