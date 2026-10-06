"""Slice 8 — review_branch: parallel reviewer fan-out + report."""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def test_review_branch_runs_reviewers_in_parallel(
        workflow_test_harness, tmp_path, monkeypatch):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.review_branch", None)
    mod = importlib.import_module("aegis.workflows.review_branch")
    monkeypatch.setattr(
        "aegis.workflows.review_branch.diff_vs",
        lambda base: "diff --git a/x b/x\n+ change\n")
    monkeypatch.setattr(
        "aegis.workflows.review_branch.branch_slug", lambda: "test-branch")

    harness = workflow_test_harness(
        host="h",
        subagent_replies={
            "security-reviewer": "security: lgtm",
            "api-reviewer": "api: lgtm",
            "test-reviewer": "tests: lgtm",
        },
        cwd=tmp_path,
        config={"reviewers": ["security-reviewer", "api-reviewer",
                              "test-reviewer"]})
    result = await mod.review_branch(harness.engine)
    written = (tmp_path / result).read_text()
    assert "security-reviewer" in written
    assert "api-reviewer" in written
    assert "test-reviewer" in written
    assert set(harness.spawned_profiles) >= {
        "security-reviewer", "api-reviewer", "test-reviewer"}


async def test_review_branch_skips_empty_diff(
        workflow_test_harness, monkeypatch, tmp_path):
    import importlib
    import sys
    sys.modules.pop("aegis.workflows.review_branch", None)
    mod = importlib.import_module("aegis.workflows.review_branch")
    monkeypatch.setattr(
        "aegis.workflows.review_branch.diff_vs", lambda base: "")
    harness = workflow_test_harness(host="h", cwd=tmp_path)
    result = await mod.review_branch(harness.engine)
    assert result == "no diff vs base"
    assert harness.spawned_profiles == []
