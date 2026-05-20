from __future__ import annotations

import pytest

from aegis.workflow import run_workflow, workflow, WorkflowError
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


class _StubBridge:
    queue_manager = None
    inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return []


async def _run(name, kwargs, **kw):
    return await run_workflow(
        name, kwargs,
        bridge=_StubBridge(), queue_manager=None, inbox_router=None,
        **kw)


async def test_runner_success_returns_status_ok(tmp_path):
    @workflow
    async def echo_back(engine, *, x):
        return x.upper()
    out = await _run("echo_back", {"x": "alex"}, state_dir=tmp_path)
    assert out["status"] == "ok"
    assert out["result"] == "ALEX"
    assert "workflow_run_id" in out


async def test_runner_workflow_error_returns_status_error(tmp_path):
    @workflow
    async def expected_fail(engine):
        raise WorkflowError("predicate violated: x")
    out = await _run("expected_fail", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "predicate violated: x" in out["error"]
    assert "workflow_run_id" in out


async def test_runner_unexpected_exception_tags_unexpected(tmp_path):
    @workflow
    async def crash(engine):
        raise ValueError("oh no")
    out = await _run("crash", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "unexpected: ValueError: oh no" in out["error"]


async def test_runner_unknown_workflow_returns_error_with_listing(tmp_path):
    @workflow
    async def alpha(engine): return None
    out = await _run("ghost", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "unknown workflow" in out["error"]
    assert "alpha" in out["error"]


async def test_runner_caller_handle_threaded_to_engine(tmp_path):
    captured = {}
    @workflow
    async def grab_caller(engine):
        captured["caller"] = engine.caller_handle
    out = await _run("grab_caller", {},
                     caller_handle="lucid-knuth", state_dir=tmp_path)
    assert out["status"] == "ok"
    assert captured["caller"] == "lucid-knuth"
