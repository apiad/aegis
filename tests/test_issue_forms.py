"""The issue forms in .github/ISSUE_TEMPLATE agree with the feedback lint."""

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from .test_feedback_lint import lint

FORMS = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"
KINDS = {
    "bug": "bug",
    "feature": "enhancement",
    "docs": "documentation",
    "question": "question",
}


def form(kind: str) -> dict:
    return YAML(typ="safe").load((FORMS / f"{kind}.yml").read_text())


def fields(kind: str) -> list[dict]:
    return [item for item in form(kind)["body"] if "id" in item]


@pytest.mark.parametrize("kind", KINDS)
def test_each_form_sets_its_label_and_has_a_required_version_field(kind):
    f = form(kind)
    assert f["name"] and f["description"]
    assert f["labels"] == [KINDS[kind], "feedback"]
    (version,) = [i for i in fields(kind) if i["id"] == "version"]
    assert version["type"] == "input"
    assert version["validations"]["required"] is True


@pytest.mark.parametrize("kind", KINDS)
def test_every_id_is_lowercase_with_hyphens(kind):
    for item in fields(kind):
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", item["id"]), item["id"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_required_labels_match_the_linter(kind):
    required = [
        i["attributes"]["label"]
        for i in fields(kind)
        if i.get("validations", {}).get("required")
    ]
    assert required == lint.REQUIRED[kind]
    assert lint.LABELS == KINDS


def test_blank_issues_stay_open():
    config = YAML(typ="safe").load((FORMS / "config.yml").read_text())
    assert config["blank_issues_enabled"] is True


def test_the_bug_steps_prefill_keeps_one_line_per_step():
    (steps,) = [i for i in fields("bug") if i["id"] == "steps"]
    assert steps["attributes"]["value"].splitlines() == ["1.", "2.", "3."]
