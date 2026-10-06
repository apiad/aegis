"""Slice 9 — tdd_cycle: write_test → implement → review with predicates."""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_tdd_cycle_writes_test_implements_reviews(
        workflow_test_harness, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.tdd_cycle", None)
    mod = importlib.import_module("aegis.workflows.tdd_cycle")

    harness = workflow_test_harness(
        host="h",
        subagent_replies={"implementer": "ok", "reviewer": "lgtm"},
        bash_sequence=[
            {"exit": 0, "stdout": "FAIL test_x", "stderr": ""},
            {"exit": 0, "stdout": "1 passed", "stderr": ""},
        ],
        cwd=tmp_path)
    result = await mod.tdd_cycle(harness.engine, feature="rate_limit",
                                 test_path="tests/test_rate_limit.py")
    assert "complete" in result
    assert harness.spawned_profiles.count("implementer") >= 2
    assert harness.spawned_profiles.count("reviewer") == 1
