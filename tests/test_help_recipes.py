"""The recipes page (help/workflow-recipes.md): each recipe is one YAML block
that lints whole against the real lint, names every step, and stays short.
The lint itself refuses a value quoted into a command."""

import re

import pytest

from aegis.helppages import load
from aegis.workflow_lang import parse, walk

AGENTS = {"opus", "sonnet"}  # the names the page tells readers to replace
PAGE = next(p for p in load() if p.id == "workflow-recipes")
RECIPES = [s for s in PAGE.sections if s.slug]
YAML = re.compile(r"```yaml\n(.*?)```", re.S)
MAX_LINES = 50


def test_the_page_carries_the_recipes_the_spec_names():
    assert [s.slug for s in RECIPES] == [
        "implement-review-and-fix-until-approved",
        "merge-on-green-ci-after-the-person-agrees",
        "one-step-per-item-a-few-at-a-time",
        "wait-for-another-branch-s-early-output",
        "a-saved-workflow-every-night",
        "poll-until-something-is-true",
        "let-a-judge-decide-before-going-on",
    ]


@pytest.mark.parametrize("section", RECIPES, ids=lambda s: s.slug)
def test_each_recipe_lints_whole_names_every_step_and_is_short(section):
    (text,) = YAML.findall(section.text)
    wf, errors = parse(text, AGENTS)
    assert [f"{e.path}: {e.message}" for e in errors] == []
    assert [n.key for n in walk(wf.root) if n.generated] == []
    assert len(text.splitlines()) <= MAX_LINES


def recipe(slug: str):
    (text,) = YAML.findall(next(s for s in RECIPES if s.slug == slug).text)
    wf, _ = parse(text, AGENTS)
    return text, wf


def test_green_counts_a_skipped_check_and_red_a_cancelled_one():
    """gh's buckets are pass, fail, pending, skipping and cancel: a skipped
    check never holds green back, and a cancelled one is fixed like a failure."""
    for section in RECIPES:
        for line in section.text.splitlines():
            if '.bucket == \\"pass\\"' in line:
                assert '.bucket == \\"skipping\\"' in line, section.slug
            if '.bucket == \\"fail\\"' in line:
                assert '.bucket == \\"cancel\\"' in line, section.slug


def test_the_ci_loop_judges_ci_on_the_push_of_its_last_fix():
    """until reads CI after the round's own wait for it, so a fix is never
    the last thing a round does: the third fix is verified too."""
    _, wf = recipe("merge-on-green-ci-after-the-person-agrees")
    (loop,) = [n for n in walk(wf.root) if n.kind == "loop"]
    steps = [c.id for c in loop.children[0].children]
    assert steps.index("finished") > steps.index("if-red")
