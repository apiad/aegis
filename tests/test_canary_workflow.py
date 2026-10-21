"""The canary's workflow, script and version modes agree (#501).

The issues step was skipped on every run for days because its condition looked
for ``.canary/junit.xml`` while canary.sh writes ``junit-1.xml`` and
``junit-2.xml``: no canary issue was ever opened.
"""

from __future__ import annotations

import re
import sys
from fnmatch import fnmatch
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import canary_versions  # noqa: E402

from aegis.harness_versions import ranges  # noqa: E402


def _step(name: str) -> dict:
    wf = YAML(typ="safe").load((REPO / ".github/workflows/canary.yml").read_text())
    return next(s for s in wf["jobs"]["canary"]["steps"] if s.get("name") == name)


def test_the_issues_step_runs_on_the_junit_files_the_script_writes():
    written = re.findall(
        r'\$AEGIS_CANARY_REPORT/(junit[^"]*\.xml)',
        (REPO / "scripts/canary.sh").read_text(),
    )
    assert written, "canary.sh writes no junit file"
    globs = re.findall(r"hashFiles\('\.canary/([^']+)'\)", _step("issues")["if"])
    assert globs
    for name in written:
        assert any(fnmatch(name, g) for g in globs), (name, globs)


def test_the_issues_step_runs_on_a_schedule_and_on_latest_only():
    cond = _step("issues")["if"]
    assert "(inputs.versions || 'latest') == 'latest'" in cond


def test_specs_install_latest_or_an_end_of_the_tested_range():
    rs = ranges()
    assert canary_versions.specs("latest") == [
        f"{r.package}@latest" for r in rs.values()
    ]
    assert canary_versions.specs("max") == [
        f"{r.package}@{r.max_s}" for r in rs.values()
    ]
    assert canary_versions.specs("min") == [
        f"{r.package}@{r.min_s}" for r in rs.values()
    ]
