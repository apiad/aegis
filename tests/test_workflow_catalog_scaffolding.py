"""Slice 5 — catalog package scaffolding + _lib helpers."""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_imports_register_seeds():
    # Re-import the catalog so the @workflow decorators fire under a
    # clean registry (the autouse fixture clears _REGISTRY each test).
    import importlib
    import sys
    for mod_name in (
        "aegis.workflows.brainstorm_to_spec",
        "aegis.workflows.execute_plan",
        "aegis.workflows.review_branch",
        "aegis.workflows.tdd_cycle",
    ):
        mod = sys.modules.get(mod_name)
        if mod is None:
            importlib.import_module(mod_name)
        else:
            importlib.reload(mod)
    from aegis.workflow import REGISTRY
    assert "brainstorm_to_spec" in REGISTRY
    assert "execute_plan" in REGISTRY
    assert "review_branch" in REGISTRY
    assert "tdd_cycle" in REGISTRY


def test_plan_parser_extracts_slices(tmp_path):
    from aegis.workflows._lib.plan_parser import parse_plan
    p = tmp_path / "plan.md"
    p.write_text(
        "# Plan\n\n"
        "## Slice 1 — first\nbody1\n\n"
        "## Slice 2 — second\nbody2\n")
    plan = parse_plan(p)
    assert plan.title == "Plan"
    assert len(plan.tasks) == 2
    assert plan.tasks[0].id == "slice-1"
    assert plan.tasks[0].title == "first"
    assert plan.tasks[0].body.startswith("body1")
    assert plan.tasks[1].id == "slice-2"


def test_plan_parser_accepts_ascii_hyphen(tmp_path):
    from aegis.workflows._lib.plan_parser import parse_plan
    p = tmp_path / "plan.md"
    p.write_text("# Plan\n\n## Slice 3 - third\nbody3\n")
    plan = parse_plan(p)
    assert len(plan.tasks) == 1
    assert plan.tasks[0].title == "third"


def test_spec_renderer_includes_qa_pairs():
    from aegis.workflows._lib.spec_renderer import render_spec_prompt
    p = render_spec_prompt("auth", {"why": "for users", "how": "JWT"})
    assert "Topic: auth" in p
    assert "Q: why" in p and "A: for users" in p
    assert "Q: how" in p and "A: JWT" in p


def test_spec_renderer_slugify():
    from aegis.workflows._lib.spec_renderer import slugify
    assert slugify("Foo Bar Baz") == "foo-bar-baz"
    assert slugify("auth/oauth refactor!") == "auth-oauth-refactor"


def test_options_formatter():
    from aegis.workflows._lib.options import format_options
    out = format_options(["red", "blue", "green"])
    assert out == "  1. red\n  2. blue\n  3. green"
