"""Tests for comment-preserving YAML edits."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.edit import set_schedule_enabled, toggle_schedule_enabled


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
