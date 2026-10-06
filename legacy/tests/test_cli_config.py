"""Tests for `aegis config ...` CLI verbs."""
from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def _chdir(p: Path) -> Path:
    prev = Path.cwd()
    os.chdir(p)
    return prev


def _seed_minimal(p: Path) -> None:
    (p / ".aegis.yaml").write_text(
        "default_agent: main\n"
        "agents:\n"
        "  main:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
        "    effort: high\n"
    )


# --- show ------------------------------------------------------------

def test_show_minimal_yaml(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "show"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    assert "default_agent: main" in res.output
    assert "provider: claude-code" in res.output


def test_show_empty_directory_exits_nonzero(tmp_path: Path) -> None:
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "show"])
    finally:
        os.chdir(prev)
    assert res.exit_code != 0
    assert "no .aegis.yaml" in res.output.lower()
    assert "aegis config agent add" in res.output


def test_show_json(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "show", "--json"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    parsed = json.loads(res.output)
    assert parsed["default_agent"] == "main"
    assert "main" in parsed["agents"]


# --- agent list ------------------------------------------------------

def test_agent_list_table(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "agent", "list"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    assert "main" in res.output
    assert "claude-code" in res.output
    assert "opus" in res.output


def test_agent_list_empty(tmp_path: Path) -> None:
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "agent", "list"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0
    assert ("no agents" in res.output.lower()
            or "aegis config agent add" in res.output)


# --- agent add -------------------------------------------------------

def test_agent_add_writes_file_when_missing(tmp_path: Path) -> None:
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "agent", "add", "main",
                  "--provider", "claude-code",
                  "--model", "opus",
                  "--effort", "high"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    assert (tmp_path / ".aegis.yaml").exists()
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "default_agent: main" in text
    assert "provider: claude-code" in text


def test_agent_add_validates_provider(tmp_path: Path) -> None:
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "agent", "add", "main",
                  "--provider", "imaginary",
                  "--model", "x"])
    finally:
        os.chdir(prev)
    assert res.exit_code != 0
    assert "imaginary" in res.output


def test_agent_add_duplicate_fails(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "agent", "add", "main",
                  "--provider", "claude-code",
                  "--model", "haiku"])
    finally:
        os.chdir(prev)
    assert res.exit_code != 0
    assert "main" in res.output


# --- agent remove ----------------------------------------------------

def test_agent_remove_drops_entry(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    # Add a second agent + switch default, so we can remove main.
    prev = _chdir(tmp_path)
    try:
        runner.invoke(
            app, ["config", "agent", "add", "fast",
                  "--provider", "gemini",
                  "--model", "gemini-3-flash-preview"])
        runner.invoke(app, ["config", "default-agent", "fast"])
        res = runner.invoke(app, ["config", "agent", "remove", "main"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "main:" not in text
    assert "fast:" in text


def test_agent_remove_referenced_by_default_agent_fails(
        tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(app, ["config", "agent", "remove", "main"])
    finally:
        os.chdir(prev)
    assert res.exit_code != 0
    assert "default_agent" in res.output


# --- queue add / list / remove ---------------------------------------

def test_queue_add_basic(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "queue", "add", "impl",
                  "--agent", "main", "--max-parallel", "2"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "impl:" in text and "max_parallel: 2" in text


def test_queue_add_with_budgets(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "queue", "add", "impl",
                  "--agent", "main", "--max-parallel", "1",
                  "--budget", "usd:1.00:1h",
                  "--budget", "output_tokens:500000:1h"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "budgets:" in text
    assert "usd:" in text
    assert "output_tokens:" in text


def test_queue_add_rejects_unknown_agent(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        res = runner.invoke(
            app, ["config", "queue", "add", "impl",
                  "--agent", "ghost", "--max-parallel", "1"])
    finally:
        os.chdir(prev)
    assert res.exit_code != 0
    assert "ghost" in res.output


def test_queue_list(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        runner.invoke(app, ["config", "queue", "add", "impl",
                            "--agent", "main", "--max-parallel", "1"])
        res = runner.invoke(app, ["config", "queue", "list"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    assert "impl" in res.output


def test_queue_remove(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        runner.invoke(app, ["config", "queue", "add", "impl",
                            "--agent", "main", "--max-parallel", "1"])
        res = runner.invoke(app, ["config", "queue", "remove", "impl"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 0, res.output
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "impl:" not in text


# --- plugin-dir ------------------------------------------------------

def test_plugin_dir_add_list_remove(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    prev = _chdir(tmp_path)
    try:
        r1 = runner.invoke(
            app, ["config", "plugin-dir", "add", "my_plugins"])
        r2 = runner.invoke(app, ["config", "plugin-dir", "list"])
        r3 = runner.invoke(
            app, ["config", "plugin-dir", "remove", "my_plugins"])
    finally:
        os.chdir(prev)
    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    assert "my_plugins" in r2.output
    assert r3.exit_code == 0, r3.output
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "my_plugins" not in text
