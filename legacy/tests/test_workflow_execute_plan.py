"""Slice 7 — execute_plan: parse plan → dispatch implementer per task."""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_execute_plan_dispatches_subagent_per_task(
        workflow_test_harness, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.execute_plan", None)
    mod = importlib.import_module("aegis.workflows.execute_plan")

    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        "# Plan\n\n"
        "## Slice 1 — first\nbody1\n\n"
        "## Slice 2 — second\nbody2\n")
    harness = workflow_test_harness(
        host="h",
        subagent_replies={"implementer": "done"},
        cwd=tmp_path)
    result = await mod.execute_plan(harness.engine, plan_path=str(plan_path))
    assert "2/2" in result
    assert harness.spawned_profiles.count("implementer") == 2
    assert sorted(harness.closed_handles) == sorted(harness.spawned_handles)


async def test_execute_plan_skips_done_tasks_on_resume(
        workflow_test_harness, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.execute_plan", None)
    mod = importlib.import_module("aegis.workflows.execute_plan")

    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\n\n## Slice 1 — a\n\n## Slice 2 — b\n")
    harness = workflow_test_harness(
        host="h", workflow_id="resumed",
        initial_state={"phase": "tasks",
                       "plan_path": str(plan_path),
                       "tasks": [{"id": "slice-1", "title": "a", "body": ""},
                                 {"id": "slice-2", "title": "b", "body": ""}],
                       "done": ["slice-1"]},
        subagent_replies={"implementer": "ok"},
        cwd=tmp_path)
    result = await mod.execute_plan(harness.engine, plan_path=str(plan_path))
    assert harness.spawned_profiles.count("implementer") == 1
    assert "2/2" in result
