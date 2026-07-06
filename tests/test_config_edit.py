"""Tests for comment-preserving YAML edits."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import ConfigError
from aegis.config.edit import (
    add_agent,
    add_plugin_dir,
    add_queue,
    remove_agent,
    remove_plugin_dir,
    remove_queue,
    set_default_agent,
    set_schedule_enabled,
    toggle_schedule_enabled,
)


def test_toggle_overlay(tmp_path: Path) -> None:
    overlay_dir = tmp_path / ".aegis" / "schedules"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "eod.yaml").write_text(
        "# overlay header — must be preserved\n"
        "workflow: prompt\n"
        "args: {agent: c, text: hi}\n"
        "cron: '0 2 * * *'\n"
        "enabled: true\n"
    )
    new = toggle_schedule_enabled(tmp_path, "eod")
    assert new is False
    text = (overlay_dir / "eod.yaml").read_text()
    assert "enabled: false" in text
    assert "overlay header — must be preserved" in text


def test_set_enabled_inline(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "# top comment\n"
        "schedules:\n"
        "  eod:\n"
        "    workflow: prompt\n"
        "    cron: '0 2 * * *'\n"
        "    enabled: true\n"
    )
    new = set_schedule_enabled(tmp_path, "eod", False)
    assert new is False
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "enabled: false" in text
    assert "top comment" in text


def test_toggle_inline_round_trip(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "schedules:\n"
        "  eod:\n"
        "    workflow: prompt\n"
        "    enabled: false\n"
    )
    assert toggle_schedule_enabled(tmp_path, "eod") is True
    assert toggle_schedule_enabled(tmp_path, "eod") is False


def test_toggle_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        toggle_schedule_enabled(tmp_path, "ghost")


def test_set_unknown_schedule_raises(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "schedules:\n  eod:\n    workflow: prompt\n    enabled: true\n")
    with pytest.raises(KeyError):
        set_schedule_enabled(tmp_path, "ghost", False)


# --- add_agent / remove_agent ----------------------------------------

def test_add_agent_creates_missing_file(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "default_agent: main" in text
    assert "provider: claude-code" in text
    assert "model: opus" in text
    assert "effort: high" in text


def test_add_agent_preserves_comments_in_existing_file(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "# operator's notes — keep this header\n"
        "default_agent: existing\n"
        "agents:\n"
        "  existing:\n"
        "    provider: claude-code\n"
        "    model: sonnet\n"
    )
    add_agent(tmp_path, "fast",
              provider="gemini", model="gemini-3-flash-preview",
              permission="full")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "operator's notes — keep this header" in text
    assert "existing:" in text
    assert "fast:" in text
    assert "provider: gemini" in text


def test_add_agent_rejects_duplicate_slug(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    with pytest.raises(ConfigError, match="main"):
        add_agent(tmp_path, "main",
                  provider="claude-code", model="haiku")


def test_add_agent_rejects_bad_permission(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="permission"):
        add_agent(tmp_path, "main",
                  provider="claude-code", model="opus",
                  permission="banana")


def test_add_agent_claude_with_effort(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus",
              effort="max", permission="full")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "effort: max" in text
    assert "permission: full" in text


def test_add_agent_gemini_no_effort_field(tmp_path: Path) -> None:
    add_agent(tmp_path, "g",
              provider="gemini", model="gemini-3-flash-preview")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "provider: gemini" in text
    # effort is Claude-only; the helper must not emit it for gemini.
    assert "effort:" not in text


def test_remove_agent_drops_the_entry(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_agent(tmp_path, "fast",
              provider="gemini", model="gemini-3-flash-preview")
    remove_agent(tmp_path, "fast")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "main:" in text
    assert "fast:" not in text


def test_remove_agent_unknown_slug_fails(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    with pytest.raises(ConfigError, match="ghost"):
        remove_agent(tmp_path, "ghost")


def test_remove_agent_referenced_by_default_agent_fails(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_agent(tmp_path, "fast",
              provider="gemini", model="gemini-3-flash-preview")
    # main is the default — removing it would leave default_agent dangling.
    with pytest.raises(ConfigError, match="default_agent"):
        remove_agent(tmp_path, "main")


def test_remove_agent_referenced_by_queue_fails(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_agent(tmp_path, "spare",
              provider="claude-code", model="haiku")
    add_queue(tmp_path, "impl", agent="main", max_parallel=1)
    with pytest.raises(ConfigError, match="impl"):
        remove_agent(tmp_path, "main")


# --- add_queue / remove_queue ----------------------------------------

def test_add_queue_with_basic_fields(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_queue(tmp_path, "impl", agent="main", max_parallel=2)
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "impl:" in text
    assert "agent: main" in text
    assert "max_parallel: 2" in text


def test_add_queue_with_budgets(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_queue(tmp_path, "impl", agent="main", max_parallel=1,
              budgets=[
                  {"usd": 1.00, "window": "1h"},
                  {"output_tokens": 500000, "window": "1h"},
              ])
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "budgets:" in text
    assert "usd:" in text
    assert "output_tokens:" in text


def test_add_queue_rejects_unknown_agent(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    with pytest.raises(ConfigError, match="ghost"):
        add_queue(tmp_path, "impl", agent="ghost", max_parallel=1)


def test_add_queue_rejects_zero_max_parallel(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    with pytest.raises(ConfigError, match="max_parallel"):
        add_queue(tmp_path, "impl", agent="main", max_parallel=0)


def test_add_queue_rejects_duplicate_name(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_queue(tmp_path, "impl", agent="main", max_parallel=1)
    with pytest.raises(ConfigError, match="impl"):
        add_queue(tmp_path, "impl", agent="main", max_parallel=2)


def test_remove_queue_drops_entry(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    add_queue(tmp_path, "impl", agent="main", max_parallel=1)
    add_queue(tmp_path, "review", agent="main", max_parallel=1)
    remove_queue(tmp_path, "review")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "impl:" in text
    assert "review:" not in text


def test_remove_queue_unknown_name_fails(tmp_path: Path) -> None:
    add_agent(tmp_path, "main",
              provider="claude-code", model="opus", effort="high")
    with pytest.raises(ConfigError, match="ghost"):
        remove_queue(tmp_path, "ghost")


def _seed_one_agent(p: Path) -> None:
    add_agent(p, "main", provider="claude-code",
              model="opus", effort="high")


# --- default_agent ---------------------------------------------------

def test_set_default_agent_changes_value(tmp_path: Path) -> None:
    _seed_one_agent(tmp_path)
    add_agent(tmp_path, "fast",
              provider="gemini", model="gemini-3-flash-preview")
    set_default_agent(tmp_path, "fast")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "default_agent: fast" in text


def test_set_default_agent_rejects_unknown(tmp_path: Path) -> None:
    _seed_one_agent(tmp_path)
    with pytest.raises(ConfigError, match="ghost"):
        set_default_agent(tmp_path, "ghost")


# --- plugin_dirs -----------------------------------------------------

def test_add_plugin_dir_appends(tmp_path: Path) -> None:
    _seed_one_agent(tmp_path)
    add_plugin_dir(tmp_path, "my_plugins")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "plugin_dirs:" in text
    assert "my_plugins" in text


def test_add_plugin_dir_idempotent(tmp_path: Path) -> None:
    _seed_one_agent(tmp_path)
    add_plugin_dir(tmp_path, "x")
    add_plugin_dir(tmp_path, "x")   # no error, no duplicate
    text = (tmp_path / ".aegis.yaml").read_text()
    assert text.count("- x") == 1


def test_remove_plugin_dir_drops_path(tmp_path: Path) -> None:
    _seed_one_agent(tmp_path)
    add_plugin_dir(tmp_path, "a")
    add_plugin_dir(tmp_path, "b")
    remove_plugin_dir(tmp_path, "a")
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "- a" not in text
    assert "- b" in text
