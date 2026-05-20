from __future__ import annotations

from pathlib import Path

import pytest

from aegis.workflow import WorkflowEngine


class _StubBridge:
    queue_manager = None
    inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return ["default"]


def _engine(tmp_path: Path, **kw):
    return WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=None, inbox_router=None,
        state_dir=tmp_path, **kw)


def test_engine_exposes_name_run_id_caller(tmp_path):
    e = _engine(tmp_path, caller_handle="lucid-knuth")
    assert e.workflow_name == "t"
    assert e.workflow_run_id == "01TID"
    assert e.caller_handle == "lucid-knuth"


def test_engine_caller_defaults_to_none(tmp_path):
    e = _engine(tmp_path)
    assert e.caller_handle is None


def test_engine_log_writes_jsonl_under_state_dir(tmp_path):
    e = _engine(tmp_path)
    e.log("hello")
    e.log("world")
    log_file = tmp_path / "workflows" / "01TID.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line]
    assert len(lines) == 2
    import json
    assert json.loads(lines[0])["message"] == "hello"
    assert json.loads(lines[1])["message"] == "world"


def test_engine_log_no_state_dir_is_stderr_only(capfd):
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01TID",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=None, state_dir=None)
    e.log("only-stderr")
    captured = capfd.readouterr()
    assert "only-stderr" in captured.err
    assert "[workflow:t]" in captured.err


def test_engine_initial_state_empty(tmp_path):
    e = _engine(tmp_path)
    assert e._spawned_handles == set()
    assert e._touched_handles == set()


def test_engine_list_passthroughs(tmp_path):
    e = _engine(tmp_path)
    assert e.list_sessions() == []
    assert e.list_agents() == ["default"]
