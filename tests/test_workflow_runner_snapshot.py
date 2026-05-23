"""WorkflowRunner.snapshot() — observability surface used by the TUI
Ctrl+D dashboard's WORKFLOWS band."""
from __future__ import annotations

import asyncio

import pytest

from aegis.workflow import WorkflowRow, workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_snapshot_empty_when_no_runs(fake_bridge_with_runner, tmp_path):
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")
    runner = fake_bridge_with_runner.workflow_runner
    assert runner.snapshot() == []


async def test_snapshot_running_then_ok(fake_bridge_with_runner, tmp_path):
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")

    started = asyncio.Event()
    can_finish = asyncio.Event()

    @workflow("blocky")
    async def blocky(engine):
        started.set()
        await can_finish.wait()
        return "done"

    runner = fake_bridge_with_runner.workflow_runner
    wid = await runner.start("blocky", {}, host="h1")
    await started.wait()

    rows = runner.snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, WorkflowRow)
    assert row.id == wid
    assert row.name == "blocky"
    assert row.host == "h1"
    assert row.status == "running"
    assert row.elapsed_s >= 0
    assert row.awaiting_human is False
    assert row.result_summary is None
    assert row.error_summary is None

    can_finish.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break

    rows = runner.snapshot()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].result_summary == "done"


async def test_snapshot_error_includes_error_summary(
        fake_bridge_with_runner, tmp_path):
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")

    @workflow("crasher")
    async def crasher(engine):
        raise RuntimeError("boom")

    runner = fake_bridge_with_runner.workflow_runner
    wid = await runner.start("crasher", {}, host="h2")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break

    rows = runner.snapshot()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_summary and "boom" in rows[0].error_summary


async def test_snapshot_orders_running_first_then_recent(
        fake_bridge_with_runner, tmp_path):
    """Running rows precede terminal ones; among terminal, most
    recently finished first."""
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")

    @workflow("fast")
    async def fast(engine):
        return "f"

    @workflow("slow")
    async def slow(engine):
        await asyncio.sleep(0.5)
        return "s"

    runner = fake_bridge_with_runner.workflow_runner
    # Two fast (terminal); one slow (still running).
    f1 = await runner.start("fast", {}, host="h")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(f1).get("status") != "running":
            break
    f2 = await runner.start("fast", {}, host="h")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if runner.status(f2).get("status") != "running":
            break
    s1 = await runner.start("slow", {}, host="h")
    await asyncio.sleep(0.05)

    rows = runner.snapshot()
    assert [r.status for r in rows][0] == "running"
    # f2 finished after f1, so f2 comes before f1 in the recent tail.
    terminal_ids = [r.id for r in rows if r.status == "ok"]
    assert terminal_ids == [f2, f1]

    # Clean up the still-running slow workflow.
    await runner.cancel(s1)
