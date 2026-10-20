"""The feedback lint (.github/scripts/feedback_lint.py), on bodies shaped the way
GitHub renders a submitted issue form."""

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "feedback_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("feedback_lint", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint = _load()

GOOD = {
    "bug": {
        "What happened": "monitor_start refused progress=null with an error.",
        "What you expected": "A monitor with no progress bar.",
        "Steps to reproduce": "1. Start a session\n2. Call monitor_start with progress null",
        "Version": "aegis 2.11.0 · Claude Code 2.1.301 · Linux",
        "Evidence": "",
        "Context": "",
    },
    "feature": {
        "The problem": "I could not tell which queue worker ran on the small model.",
        "What you propose": "Show the model next to the worker on the queue card.",
        "What you do today": "",
        "Version": "aegis 2.11.0",
        "Context": "",
    },
    "docs": {
        "Page or search": "queues#cancel-a-task",
        "What is wrong or missing": "It does not say what happens to a running worker.",
        "Suggested text": "",
        "Version": "aegis 2.11.0",
    },
    "question": {
        "Question": "Can a schedule fire into a session on a linked server?",
        "What you tried": "",
        "Version": "aegis 2.11.0",
    },
}
TITLE = "Queue card should show the worker's model"


def form_body(fields: dict[str, str]) -> str:
    return "".join(
        f"### {label}\n\n{value or '_No response_'}\n\n"
        for label, value in fields.items()
    )


@pytest.mark.parametrize("kind", list(GOOD))
def test_a_complete_body_of_each_kind_passes(kind):
    assert lint.problems(kind, TITLE, form_body(GOOD[kind])) == []


def test_a_blank_or_no_response_field_is_named():
    fields = dict(GOOD["bug"], **{"Steps to reproduce": ""})
    (p,) = lint.problems("bug", TITLE, form_body(fields))
    assert "Steps to reproduce" in p
    fields = dict(GOOD["feature"], **{"What you propose": "   "})
    (p,) = lint.problems("feature", TITLE, form_body(fields))
    assert "What you propose" in p


def test_a_missing_heading_is_named():
    fields = {k: v for k, v in GOOD["docs"].items() if k != "Page or search"}
    (p,) = lint.problems("docs", TITLE, form_body(fields))
    assert "Page or search" in p


def test_the_version_must_name_an_aegis_version():
    fields = dict(GOOD["question"], Version="latest")
    (p,) = lint.problems("question", TITLE, form_body(fields))
    assert "Version" in p
    fields = dict(GOOD["question"], Version="aegis 2.11.0")
    assert lint.problems("question", TITLE, form_body(fields)) == []


def test_a_one_line_repro_is_too_thin():
    fields = dict(GOOD["bug"], **{"Steps to reproduce": "it freezes sometimes"})
    (p,) = lint.problems("bug", TITLE, form_body(fields))
    assert "Steps to reproduce" in p
    block = "```\naegis serve\n```"
    fields = dict(GOOD["bug"], **{"Steps to reproduce": block})
    assert lint.problems("bug", TITLE, form_body(fields)) == []


@pytest.mark.parametrize(
    "title", ["bug", "Feature request", "[docs]", "too short", "x" * 101]
)
def test_titles_that_say_nothing_fail(title):
    (p,) = lint.problems("feature", title, form_body(GOOD["feature"]))
    assert "title" in p.lower()


def test_crlf_bodies_read_the_same():
    fields = dict(GOOD["bug"], **{"Steps to reproduce": ""})
    lf = form_body(fields)
    crlf = lf.replace("\n", "\r\n")
    assert lint.sections(crlf) == lint.sections(lf)
    assert lint.problems("bug", TITLE, crlf) == lint.problems("bug", TITLE, lf)
    good = form_body(GOOD["bug"]).replace("\n", "\r\n")
    assert lint.problems("bug", TITLE, good) == []


def test_the_marker_wins_and_no_kind_means_skip():
    marked = form_body(GOOD["feature"]) + "<!-- aegis-feedback kind=feature -->\n"
    assert lint.kind_of(marked, ["bug"]) == "feature"
    assert lint.kind_of("anything", ["documentation", "idea"]) == "docs"
    assert lint.kind_of("anything", ["idea"]) is None
    assert lint.kind_of("<!-- aegis-feedback kind=nonsense -->", []) is None


TOKENS = [
    "ghp_" + "a1B2" * 9,
    "github_pat_" + "11ABCDEFG0" * 4,
    "sk-" + "abcdefghij" * 3,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
]


@pytest.mark.parametrize("token", TOKENS)
def test_a_secret_is_flagged_and_never_quoted(token):
    fields = dict(GOOD["bug"], Evidence=f"my config has {token} in it")
    text = form_body(fields)
    assert lint.secret(text)
    assert not any(token in p for p in lint.problems("bug", TITLE, text))
    assert not lint.secret(form_body(GOOD["bug"]))


def test_the_required_labels_match_the_help_page():
    page = (REPO / "src/aegis/help/feedback.md").read_text()
    block = re.search(r"## What each kind needs.*?```text\n(.*?)```", page, re.S)[1]
    for line in block.strip().splitlines():
        kind, _, rest = line.partition(" ")
        starred = re.findall(r"([A-Z][^·*(]*?)\*", rest)
        assert [s.strip() for s in starred] == lint.REQUIRED[kind], kind
