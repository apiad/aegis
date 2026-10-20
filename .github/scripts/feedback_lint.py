"""Lint a feedback issue: what a maintainer would still need to act on it.

Runs in .github/workflows/feedback-lint.yml with the runner's own python3, so it
uses the standard library only. A feedback issue is one an agent filed by
following the help page feedback (it ends with the kind marker) or one opened
from an issue form (it carries the form's labels: its kind and feedback). Both
have the same body: one ``### <label>`` section per form field, which is how
GitHub renders a submitted form and what the help page tells agents to write.

The lint is advice. It comments and labels; it never closes an issue, never
edits its body, and never quotes what it matched as a secret.

``check`` writes a prompt for a model review and ``finish --review-file`` merges
its answer. The workflow runs no model today: GitHub Models, which needed no
secret, was retired on 2026-07-30, and the action that replaced it needs a
Copilot token stored in the repository.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

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
FEEDBACK = "feedback"
MARKER = re.compile(r"<!--\s*aegis-feedback\s+kind=(\w+)\s*-->")
SECRETS = re.compile(
    r"ghp_\w{20,}|github_pat_\w{20,}|sk-\w{20,}|AKIA[0-9A-Z]{16}|-----BEGIN"
)
EMPTY = {"", "_No response_"}
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
BARE_TITLE = re.compile(
    r"(?i)^\W*(bug|feature( request)?|docs?|question|issue|help)\W*$"
)
COMMENT_MARK = "<!-- feedback-lint -->"
HELP_PAGE = (
    Path(__file__).resolve().parents[2] / "src" / "aegis" / "help" / "feedback.md"
)
LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S", re.M)


def kind_of(body: str, labels: list[str]) -> str | None:
    m = MARKER.search(body)
    if m:
        return m[1] if m[1] in LABELS else None
    # A kind label alone marks the maintainer's own issues too; a form adds
    # FEEDBACK beside it.
    if FEEDBACK not in labels:
        return None
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


def review_findings(raw: str | None) -> list[str] | None:
    """The model's answer as a list of findings; None when the step failed or
    answered anything but a JSON array of strings."""
    if not raw or not raw.strip():
        return None
    text = re.sub(r"^```\w*\n|\n?```$", "", raw.strip())
    try:
        found = json.loads(text)
    except ValueError:
        return None
    if not isinstance(found, list) or not all(isinstance(f, str) for f in found):
        return None
    return [f.strip()[:300] for f in found if f.strip()][:5]


def comment(
    kind: str, problems: list[str], findings: list[str] | None, secret: bool
) -> str | None:
    """The comment's text, or None when there is nothing to say."""
    missing = problems + (findings or [])
    if not missing and not secret:
        return None
    parts = [COMMENT_MARK]
    if secret:
        parts.append(
            "This issue may contain a token or a private key. Edit it to take "
            "the value out, and revoke the key: an edit does not erase the "
            "issue's history."
        )
    if missing:
        parts.append("This report is missing what a maintainer needs to act on it:")
        parts.append("\n".join(f"- {m}" for m in missing))
    parts.append(
        "Edit the issue and this check runs again. Filed by an agent? The agent "
        "can fix it with `gh issue edit`."
    )
    return "\n\n".join(parts)


def run_gh(args: list[str], stdin: str | None = None) -> str:
    return subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, check=True
    ).stdout


def apply(
    repo: str,
    number: int,
    kind: str,
    text: str | None,
    labels: list[str],
    secret: bool,
    gh=run_gh,
) -> None:
    """Label the issue and write, edit or settle the one marked comment."""
    issue = f"repos/{repo}/issues/{number}"
    # Bullets appear only when something is missing (comment above).
    missing = text is not None and "\n- " in text
    add = [x for x in (LABELS[kind], FEEDBACK) if x not in labels]
    for label, on in (("needs-info", missing), ("needs-redaction", secret)):
        if on and label not in labels:
            add.append(label)
        elif not on and label in labels:
            gh(["api", "--method", "DELETE", f"{issue}/labels/{label}"])
    if add:
        gh(
            [
                "api",
                "--method",
                "POST",
                *(a for x in add for a in ("-f", f"labels[]={x}")),
                f"{issue}/labels",
            ]
        )
    mine = next(
        (
            c
            for c in json.loads(gh(["api", "--paginate", f"{issue}/comments"]) or "[]")
            if (c.get("body") or "").startswith(COMMENT_MARK)
        ),
        None,
    )
    if text is None:
        if mine is None:
            return
        text = f"{COMMENT_MARK}\ncomplete: nothing a maintainer needs is missing."
    if mine is None:
        gh(["api", "--method", "POST", "-f", f"body={text}", f"{issue}/comments"])
    elif mine.get("body") != text:
        gh(
            [
                "api",
                "--method",
                "PATCH",
                "-f",
                f"body={text}",
                f"repos/{repo}/issues/comments/{mine['id']}",
            ]
        )


def prompt(kind: str, title: str, body: str) -> str:
    page = HELP_PAGE.read_text()
    m = re.search(r"## What each kind needs\n(.*?)\n## ", page, re.S)
    needs = m[1].strip() if m else ""
    return (
        "You review an issue filed against aegis, a workplace for coding agents. "
        "Below are what each kind of issue needs, then the issue.\n\n"
        f"{needs}\n\n--- issue (kind: {kind}) ---\ntitle: {title}\n\n{body}\n--- end ---\n\n"
        "Answer with a JSON array of short strings naming what a maintainer would "
        "still need to act on this issue; [] if nothing. Name only what is "
        "missing or too vague to act on, at most five, and nothing else."
    )


def _event() -> tuple[str, int, str, str, list[str]]:
    ev = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = ev["issue"]
    labels = [lb["name"] for lb in issue.get("labels", [])]
    return (
        ev["repository"]["full_name"],
        issue["number"],
        issue.get("title") or "",
        issue.get("body") or "",
        labels,
    )


def _output(**values: str) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        for key, value in values.items():
            if "\n" in value or key == "prompt":
                end = f"EOF_{secrets.token_hex(8)}"
                out.write(f"{key}<<{end}\n{value}\n{end}\n")
            else:
                out.write(f"{key}={value}\n")


def main(argv: list[str]) -> int:
    repo, number, title, body, labels = _event()
    kind = kind_of(body, labels)
    if argv[:1] == ["check"]:
        if kind is None:
            _output(skip="true", kind="", prompt="")
            return 0
        found = problems(kind, title, body)
        _output(
            skip="false",
            kind=kind,
            # No review while checks fail, and no body with a secret in it
            # goes to the model.
            prompt="" if found or secret(body) else prompt(kind, title, body),
        )
        return 0
    if argv[:1] == ["finish"]:
        if kind is None:
            return 0
        raw = None
        if "--review-file" in argv:
            path = Path(argv[argv.index("--review-file") + 1])
            raw = path.read_text() if path.exists() else None
        found = problems(kind, title, body)
        findings = None if found else review_findings(raw)
        has_secret = secret(f"{title}\n{body}")
        apply(
            repo,
            number,
            kind,
            comment(kind, found, findings, has_secret),
            labels,
            has_secret,
        )
        return 0
    print(
        "usage: feedback_lint.py check | finish --review-file <path>", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
