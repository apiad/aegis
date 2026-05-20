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


class _RecordingBridge:
    """Bridge that records spawn/close calls. Test double for Task 3.5."""
    queue_manager = None
    inbox_router = None
    def __init__(self):
        self.spawned: list[str] = []
        self.closed: list[str] = []
    def list_sessions(self): return []
    def list_agents(self): return ["default"]
    async def spawn(self, profile, *, handle=None):
        h = handle or f"{profile}-h{len(self.spawned)}"
        self.spawned.append(h)
        return h
    async def close(self, handle):
        self.closed.append(handle)


async def test_runner_auto_closes_spawned_handles(tmp_path):
    br = _RecordingBridge()
    @workflow
    async def spawns_two(engine):
        await engine.spawn("default", handle="a-one")
        await engine.spawn("default", handle="b-two")
        return "done"
    out = await run_workflow(
        "spawns_two", {},
        bridge=br, queue_manager=None, inbox_router=None,
        state_dir=tmp_path)
    assert out["status"] == "ok"
    assert sorted(br.closed) == ["a-one", "b-two"]


async def test_runner_auto_closes_on_error_too(tmp_path):
    br = _RecordingBridge()
    @workflow
    async def spawns_then_fails(engine):
        await engine.spawn("default", handle="leaked")
        raise WorkflowError("boom")
    out = await run_workflow(
        "spawns_then_fails", {},
        bridge=br, queue_manager=None, inbox_router=None,
        state_dir=tmp_path)
    assert out["status"] == "error"
    assert br.closed == ["leaked"]


async def test_runner_auto_drains_touched_handles(tmp_path):
    """drain() should be invoked at teardown — we assert by spying on
    the engine's drain method via a monkeypatch on a workflow that
    touched some handle through send()."""
    from aegis.queue import InboxRouter
    drained = []

    @workflow
    async def touches(engine):
        # Monkeypatch drain to record the call (still preserve behavior).
        original_drain = engine.drain
        async def spy(handle=None):
            drained.append(("call", handle))
            return await original_drain(handle)
        engine.drain = spy
        # Touch a handle (no session bound — drain becomes a no-op).
        engine.send("ghost", "hi")
        return "ok"
    out = await run_workflow(
        "touches", {},
        bridge=_StubBridge(), queue_manager=None,
        inbox_router=InboxRouter(), state_dir=tmp_path)
    assert out["status"] == "ok"
    assert drained == [("call", None)]
