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


class Gh:
    """Records each gh call; answers the comment list with ``comments``."""

    def __init__(self, comments=()):
        self.comments = list(comments)
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin=None):
        self.calls.append(args)
        if args[-1].endswith("/comments") and "--method" not in args:
            import json

            return json.dumps(self.comments)
        return "{}"

    def methods(self, method):
        return [c for c in self.calls if "--method" in c and method in c]


MARK = "<!-- feedback-lint -->"


def test_a_rerun_edits_its_own_comment_instead_of_adding_one():
    gh = Gh([{"id": 7, "body": "thanks!"}, {"id": 9, "body": MARK + "\nold"}])
    text = lint.comment("bug", ["**Version** is empty."], None, False)
    lint.apply("o/r", 5, "bug", text, [], False, gh=gh)
    (patch,) = gh.methods("PATCH")
    assert "repos/o/r/issues/comments/9" in patch
    assert not [c for c in gh.methods("POST") if c[-1].endswith("/comments")]


def test_a_first_run_with_problems_posts_one_comment_and_labels():
    gh = Gh()
    text = lint.comment("bug", ["**Version** is empty."], None, False)
    lint.apply("o/r", 5, "bug", text, [], False, gh=gh)
    posts = gh.methods("POST")
    assert any(c[-1] == "repos/o/r/issues/5/comments" for c in posts)
    labels = " ".join(" ".join(c) for c in posts)
    assert "labels[]=bug" in labels and "labels[]=needs-info" in labels


def test_a_complete_issue_clears_needs_info_and_says_complete():
    gh = Gh([{"id": 9, "body": MARK + "\nold"}])
    lint.apply("o/r", 5, "feature", None, ["enhancement", "needs-info"], False, gh=gh)
    (patch,) = gh.methods("PATCH")
    assert any("complete" in a for a in patch)
    (delete,) = gh.methods("DELETE")
    assert delete[-1] == "repos/o/r/issues/5/labels/needs-info"


def test_a_complete_issue_with_no_earlier_comment_posts_nothing():
    gh = Gh()
    lint.apply("o/r", 5, "feature", None, ["enhancement"], False, gh=gh)
    assert not [c for c in gh.methods("POST") if c[-1].endswith("/comments")]
    assert not gh.methods("PATCH") and not gh.methods("DELETE")


def test_a_failed_review_is_skipped_and_never_adds_needs_info_alone():
    for raw in (None, "", "not json", '{"a": 1}', "[1, 2]"):
        assert lint.review_findings(raw) is None
    assert lint.review_findings('```json\n["a repro"]\n```') == ["a repro"]
    assert lint.review_findings("[]") == []
    assert lint.comment("bug", [], None, False) is None
    assert "review was skipped" in lint.comment("bug", ["x"], None, False)


def test_review_findings_reach_the_comment():
    text = lint.comment("feature", [], ["Which page shows the card?"], False)
    assert text.startswith(MARK) and "Which page shows the card?" in text


def test_the_secret_comment_asks_for_an_edit_and_adds_needs_redaction():
    gh = Gh()
    text = lint.comment("bug", [], [], True)
    assert "token" in text.lower() and "edit" in text.lower()
    lint.apply("o/r", 5, "bug", text, [], True, gh=gh)
    labels = " ".join(" ".join(c) for c in gh.methods("POST"))
    assert "labels[]=needs-redaction" in labels


def event(tmp_path, body, labels=(), title=TITLE):
    import json

    p = tmp_path / "event.json"
    p.write_text(
        json.dumps(
            {
                "issue": {
                    "number": 5,
                    "title": title,
                    "body": body,
                    "labels": [{"name": n} for n in labels],
                },
                "repository": {"full_name": "o/r"},
            }
        )
    )
    return p


def outputs(path):
    text = path.read_text()
    out = dict(re.findall(r"^(\w+)=(.*)$", text, re.M))
    m = re.search(r"^prompt<<(\S+)\n(.*?)\n\1$", text, re.S | re.M)
    out["prompt"] = m[2] if m else None
    return out


def test_check_skips_an_issue_that_is_not_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event(tmp_path, None, ["idea"])))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    lint.main(["check"])
    assert outputs(tmp_path / "out")["skip"] == "true"


def test_check_writes_a_prompt_only_when_the_checks_pass(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    good = form_body(GOOD["feature"]) + "<!-- aegis-feedback kind=feature -->"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event(tmp_path, good)))
    lint.main(["check"])
    o = outputs(out)
    assert o["skip"] == "false" and o["kind"] == "feature"
    assert "What you propose" in o["prompt"] and "JSON array" in o["prompt"]
    out.unlink()
    thin = form_body(dict(GOOD["feature"], **{"The problem": ""}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event(tmp_path, thin, ["enhancement"])))
    lint.main(["check"])
    assert outputs(out)["prompt"] == ""
