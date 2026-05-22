"""Slice 3 — WorkflowRunner.resume picks up after the last checkpoint."""
from __future__ import annotations

import asyncio

import pytest

from aegis.workflow import workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_resume_from_last_checkpoint(fake_bridge_with_runner, tmp_path):
    """Workflow checkpoints, then 'crashes'. Resume continues past the
    checkpoint."""
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")

    progress: list[str] = []

    @workflow("crashy")
    async def crashy(engine, *, fail_after_checkpoint: bool):
        state = await engine.resume_state() or {"phase": "init"}
        if state["phase"] == "init":
            progress.append("before_checkpoint")
            await engine.checkpoint(
                "done_init", {"phase": "next", "data": 42})
            state = {"phase": "next", "data": 42}
            if fail_after_checkpoint:
                raise RuntimeError("simulated crash")
        if state["phase"] == "next":
            progress.append(f"after_resume_with_{state['data']}")
            return "ok"

    runner = fake_bridge_with_runner.workflow_runner
    wid = await runner.start(
        "crashy", {"fail_after_checkpoint": True}, host="h")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break
    assert "before_checkpoint" in progress
    assert "after_resume_with_42" not in progress
    assert runner.status(wid)["status"] == "error"

    await runner.resume(wid)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") in {"ok", "error"}:
            break
    assert "after_resume_with_42" in progress
    assert runner.status(wid)["status"] == "ok"


async def test_resume_unknown_id_returns_error(fake_bridge_with_runner,
                                               tmp_path):
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")
    runner = fake_bridge_with_runner.workflow_runner
    with pytest.raises(KeyError):
        await runner.resume("wf_does_not_exist")


async def test_resume_terminal_workflow_is_noop(fake_bridge_with_runner,
                                                tmp_path):
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")
    runs: list[int] = []

    @workflow("once")
    async def once(engine):
        runs.append(1)
        return "done"

    runner = fake_bridge_with_runner.workflow_runner
    wid = await runner.start("once", {}, host="h")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") == "ok":
            break
    assert runs == [1]
    await runner.resume(wid)
    await asyncio.sleep(0.05)
    assert runs == [1]
