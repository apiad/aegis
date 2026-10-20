"""Lint a feedback issue: what a maintainer would still need to act on it.

Runs in .github/workflows/feedback-lint.yml with the runner's own python3, so it
uses the standard library only. A feedback issue is one an agent filed by
following the help page feedback (it ends with the kind marker) or one opened
from an issue form (it carries the form's label). Both have the same body: one
``### <label>`` section per form field, which is how GitHub renders a submitted
form and what the help page tells agents to write.

The lint is advice. It comments and labels; it never closes an issue, never
edits its body, and never quotes what it matched as a secret.
"""

from __future__ import annotations

import re

LABELS = {
    "bug": "bug",
    "feature": "enhancement",
    "docs": "documentation",
    "question": "question",
}
# The required fields' labels, as in the help page's table and the forms
# (tests/test_feedback_lint.py and tests/test_issue_forms.py keep the three
# in step).
REQUIRED = {
    "bug": ["What happened", "What you expected", "Steps to reproduce", "Version"],
    "feature": ["The problem", "What you propose", "Version"],
    "docs": ["Page or search", "What is wrong or missing", "Version"],
    "question": ["Question", "Version"],
}
MARKER = re.compile(r"<!--\s*aegis-feedback\s+kind=(\w+)\s*-->")
SECRETS = re.compile(
    r"ghp_\w{20,}|github_pat_\w{20,}|sk-\w{20,}|AKIA[0-9A-Z]{16}|-----BEGIN"
)
EMPTY = {"", "_No response_"}
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
BARE_TITLE = re.compile(
    r"(?i)^\W*(bug|feature( request)?|docs?|question|issue|help)\W*$"
)
LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S", re.M)


def kind_of(body: str, labels: list[str]) -> str | None:
    m = MARKER.search(body)
    if m:
        return m[1] if m[1] in LABELS else None
    by_label = {label: kind for kind, label in LABELS.items()}
    return next((by_label[name] for name in labels if name in by_label), None)


def sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    head = None
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.startswith("### "):
            head = line[4:].strip()
            out[head] = ""
        elif head is not None:
            out[head] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def secret(text: str) -> bool:
    return bool(SECRETS.search(text))


def problems(kind: str, title: str, body: str) -> list[str]:
    found = []
    title = title.strip()
    if not 10 <= len(title) <= 100 or BARE_TITLE.match(title):
        found.append(
            "The title should say what happened or what is wanted, "
            "in 10 to 100 characters."
        )
    have = sections(body)
    for label in REQUIRED[kind]:
        text = have.get(label)
        if text is None:
            found.append(f"**{label}** is missing: add a `### {label}` section.")
        elif text in EMPTY:
            found.append(f"**{label}** is empty.")
        elif label == "Version" and not VERSION.search(text):
            found.append(
                "**Version** should name the aegis version (`aegis --version`)."
            )
        elif label == "Steps to reproduce" and not (
            "```" in text or len(LIST_ITEM.findall(text)) >= 2
        ):
            found.append(
                "**Steps to reproduce** should be numbered steps from a fresh "
                "session, or a command block."
            )
    return found
