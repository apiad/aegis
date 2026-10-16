"""Turn a canary run into GitHub issues: one per failing test and setup.

Reads what scripts/canary.sh left in the report folder: ``junit*.xml`` for what
ran and passed, one ``<test>.json`` per failure (tests/canary/conftest.py) and
``versions.txt``. A failure with no open ``canary`` issue opens one; one that
has an issue gets a comment naming this run, so a break that lasts a week is
one issue with seven comments. An open issue whose test passed this run gets a
comment and is closed.

    uv run python scripts/canary_issue.py .canary --run-url URL --sha SHA [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LABEL = "canary"
BODY_MAX = 60_000  # GitHub refuses a body over 65,536 characters
NODE = "tests/canary/test_harnesses.py::"


def gh(*args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, input=input
    ).stdout


def title_key(test: str) -> str:
    """``canary: test_x[codex]``: the part of a title that stays the same
    from one night to the next, whatever the version."""
    return f"canary: {test.split('::')[-1]}"


def passed_tests(report: Path) -> set[str]:
    """Every test that passed in some pass of the run (canary.sh reruns a
    failure once; a test that failed for good has its report)."""
    out = set()
    for junit in sorted(report.glob("junit*.xml")):
        for case in ET.parse(junit).iter("testcase"):
            if not any(c.tag in ("failure", "error", "skipped") for c in case):
                out.add(case.get("name", ""))
    return out


def fence(lang: str, text: str) -> str:
    return f"```{lang}\n{text.replace('```', '` ` `')}\n```"


def body(report: dict, versions: str, run_url: str, sha: str) -> str:
    test = report["test"]
    node = NODE + test.split("::")[-1]
    lines = [
        f"The canary failed on **{report.get('setup') or report['harness']}**,"
        f" {report['version']}, aegis `{sha[:10]}`.",
        "",
        f"Run: {run_url} (the `canary-report` artifact holds the full JSON).",
        "",
        "## Reproduce",
        "",
        fence(
            "bash",
            f"git checkout {sha}\nscripts/canary.sh '{node}'",
        ),
        "",
        "`scripts/canary.sh` installs the latest stable release of each harness"
        " into `~/.cache/aegis-canary`; pin one with"
        " `npm install --prefix ~/.cache/aegis-canary/npm <package>@<version>`"
        " and `AEGIS_CANARY_NO_INSTALL=1`.",
        "",
        "## Versions",
        "",
        fence("text", versions.strip()),
        "",
        "## The failure",
        "",
        fence("text", (report.get("crash") or "").strip()),
        "",
        "<details><summary>Traceback, last lines</summary>",
        "",
        fence("text", report["error"][-4000:]),
        "",
        "</details>",
        "",
        "## What the fake model was asked, and answered",
        "",
        "One line per request: the API, the tools the assistant had called since"
        " the person's message, and the reply. A tool aegis expected but the"
        " harness did not offer shows as a `CANARY:` reply.",
        "",
    ]
    log = report.get("model_log") or []
    rows = [
        f"{i}. {x.get('api')} {x.get('model') or x.get('path') or ''}"
        f" called={x.get('called')} reply={json.dumps(x.get('reply'))[:300]}"
        + (
            f" results={[r[:200] for r in x.get('tool_results', [])]}"
            if x.get("tool_results")
            else ""
        )
        for i, x in enumerate(log[-40:], 1)
    ]
    lines += [
        fence("text", "\n".join(rows) or "(no request reached the fake model)"),
        "",
    ]
    for s in report.get("sessions") or []:
        lines += [f"## Session {s['handle']} ({s['status']})", ""]
        entries = [
            f"{e['kind']:7} {e.get('status') or '':8} {e.get('title') or ''} "
            + (e.get("summary") or e.get("md") or "")[:300].replace("\n", " ")
            for e in s.get("entries") or []
        ]
        lines += [fence("text", "\n".join(entries[-40:])), ""]
        if s.get("stderr", "").strip():
            lines += [
                "Harness stderr, last lines:",
                "",
                fence("text", s["stderr"][-3000:]),
                "",
            ]
    text = "\n".join(lines)
    return (
        text
        if len(text) <= BODY_MAX
        else text[:BODY_MAX] + "\n\n(cut; see the artifact)"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--run-url", default="(local run)")
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    versions = (
        (a.report / "versions.txt").read_text()
        if (a.report / "versions.txt").exists()
        else ""
    )
    failures = [json.loads(p.read_text()) for p in sorted(a.report.glob("*.json"))]
    if a.dry_run:
        for r in failures:
            print(f"--- {title_key(r['test'])} on {r['version']}")
            print(body(r, versions, a.run_url, a.sha))
        print(f"{len(failures)} failures")
        return 0

    gh("label", "create", LABEL, "--color", "d93f0b", "--force",
       "--description", "a nightly real-harness canary failure")  # fmt: skip
    open_issues = json.loads(
        gh(
            "issue",
            "list",
            "--label",
            LABEL,
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title",
        )  # fmt: skip
    )
    by_key = {i["title"].split(" fails on ")[0]: i["number"] for i in open_issues}
    failed_keys = set()
    for r in failures:
        key = title_key(r["test"])
        failed_keys.add(key)
        text = body(r, versions, a.run_url, a.sha)
        if key in by_key:
            gh("issue", "comment", str(by_key[key]), "--body-file", "-",
               input=f"Still failing.\n\n{text}")  # fmt: skip
        else:
            gh("issue", "create", "--label", LABEL, "--title",
               f"{key} fails on {r['version']}", "--body-file", "-", input=text)  # fmt: skip
    passed = {title_key(n) for n in passed_tests(a.report)}
    for key, number in by_key.items():
        if key in passed and key not in failed_keys:
            gh("issue", "close", str(number), "--comment",
               f"Passes again in {a.run_url} (aegis `{a.sha[:10]}`).\n\n"
               + fence("text", versions.strip()))  # fmt: skip
    print(f"{len(failures)} failures, {len(by_key)} issues were open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
