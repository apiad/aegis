"""Slice 6 — brainstorm_to_spec: 5-question interview → spec doc."""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_brainstorm_to_spec_happy_path(workflow_test_harness, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.brainstorm_to_spec", None)
    mod = importlib.import_module("aegis.workflows.brainstorm_to_spec")

    harness = workflow_test_harness(
        host="h",
        human_replies=[
            "answer 1", "answer 2", "answer 3", "answer 4", "answer 5",
        ],
        subagent_replies={"spec_writer": "# Spec\n\nDrafted content."},
        cwd=tmp_path)
    result = await mod.brainstorm_to_spec(harness.engine, topic="testing")
    assert result.endswith(".md")
    written = (tmp_path / result).read_text()
    assert "# Spec" in written
    assert "spec_writer" in harness.spawned_profiles
    assert sorted(harness.spawned_handles) == sorted(harness.closed_handles)


async def test_brainstorm_to_spec_asks_for_topic_when_missing(
        workflow_test_harness, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.brainstorm_to_spec", None)
    mod = importlib.import_module("aegis.workflows.brainstorm_to_spec")

    harness = workflow_test_harness(
        host="h",
        human_replies=["my topic", "a", "b", "c", "d", "e"],
        subagent_replies={"spec_writer": "# Spec"},
        cwd=tmp_path)
    result = await mod.brainstorm_to_spec(harness.engine)
    assert "my-topic" in result
