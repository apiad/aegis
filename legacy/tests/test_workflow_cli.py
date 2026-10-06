from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


_PLUGIN = """\
from aegis.workflow import workflow

@workflow
async def hello(engine, *, name="world"):
    engine.log(f"Hi {name}!")
    return f"greeted {name}"
"""

_MIN_YAML = """\
default_agent: default
agents:
  default:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
"""


@pytest.fixture
def sample_project(tmp_path, monkeypatch):
    """Write a minimal .aegis.yaml plus a .aegis/plugins/*.py that
    registers one workflow."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(_MIN_YAML)
    plugin_dir = tmp_path / ".aegis" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hello.py").write_text(_PLUGIN)
    return tmp_path


def test_workflow_list_enumerates_registry(sample_project):
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert "hello" in res.output


def test_workflow_run_known_succeeds(sample_project):
    res = CliRunner().invoke(
        app, ["workflow", "run", "hello", "--name=Alex"])
    assert res.exit_code == 0
    assert "ok" in res.output
    assert "greeted Alex" in res.output


def test_workflow_run_unknown_exits_nonzero_with_listing(sample_project):
    res = CliRunner().invoke(app, ["workflow", "run", "ghost"])
    assert res.exit_code != 0
    assert "ghost" in res.output
    assert "hello" in res.output    # available list


def test_workflow_run_writes_log_jsonl(sample_project):
    res = CliRunner().invoke(
        app, ["workflow", "run", "hello", "--name=Alex"])
    assert res.exit_code == 0
    from pathlib import Path
    log_dir = Path.cwd() / ".aegis" / "state" / "workflows"
    assert log_dir.exists()
    log_files = list(log_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "Hi Alex!" in content


def test_workflow_list_empty_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(_MIN_YAML)
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert ("no workflows" in res.output.lower()
            or res.output.strip() == "")
