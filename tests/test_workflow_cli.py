from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.workflow import workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


@pytest.fixture
def sample_aegis_py(tmp_path, monkeypatch):
    """Write a minimal .aegis.py that registers one workflow."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.py").write_text("""
from aegis import Agent
from aegis.workflow import workflow

agents = {"default": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
default_agent = "default"

@workflow
async def hello(engine, *, name="world"):
    engine.log(f"Hi {name}!")
    return f"greeted {name}"
""")
    return tmp_path


def test_workflow_list_enumerates_registry(sample_aegis_py):
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert "hello" in res.output


def test_workflow_run_known_succeeds(sample_aegis_py):
    res = CliRunner().invoke(
        app, ["workflow", "run", "hello", "--name=Alex"])
    assert res.exit_code == 0
    assert "ok" in res.output
    assert "greeted Alex" in res.output


def test_workflow_run_unknown_exits_nonzero_with_listing(sample_aegis_py):
    res = CliRunner().invoke(app, ["workflow", "run", "ghost"])
    assert res.exit_code != 0
    assert "ghost" in res.output
    assert "hello" in res.output    # available list


def test_workflow_run_writes_log_jsonl(sample_aegis_py, tmp_path_factory):
    # The sample_aegis_py fixture monkeypatches cwd; the runner writes
    # .aegis/state/workflows/<run_id>.jsonl under project root.
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
    (tmp_path / ".aegis.py").write_text("""
from aegis import Agent
agents = {"default": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
default_agent = "default"
""")
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert ("no workflows" in res.output.lower()
            or res.output.strip() == "")
