"""AGENTS.md's done line names the make targets an agent runs before calling a
change done. Every worker trusts that line, so it has to run every test CI runs:
when it named `make check` alone and claimed the browser tests, `check` deselected
every one of them, and a worker reported green on a change that broke one (#267).

This reads the done line, the Makefile and the CI workflow, and checks marker
expressions rather than collecting tests, so it costs nothing in the fast lane.
"""

import itertools
import re
from pathlib import Path

from _pytest.mark.expression import Expression

ROOT = Path(__file__).parent.parent
MARKERS = ("slow", "browser", "live")


def _done_targets() -> list[str]:
    text = (ROOT / "AGENTS.md").read_text()
    section = text.split("## What done means", 1)[1]
    first = re.search(r"^1\. (.*?)^2\. ", section, re.M | re.S).group(1)
    return list(dict.fromkeys(re.findall(r"`make ([\w-]+)`", first)))


def _makefile() -> dict[str, tuple[list[str], list[str]]]:
    """Each target's prerequisites and recipe lines."""
    rules: dict[str, tuple[list[str], list[str]]] = {}
    current = None
    for line in (ROOT / "Makefile").read_text().splitlines():
        if line.startswith("\t") and current:
            rules[current][1].append(line.strip())
        elif m := re.match(r"^([\w-]+):(?!=)(.*)$", line):
            current = m.group(1)
            rules[current] = (m.group(2).split(), [])
        elif not line.startswith("#"):
            current = None
    return rules


def _pytest_selection(command: str) -> str | None:
    """The -m expression of a pytest command; "" when it selects everything."""
    if "pytest" not in command:
        return None
    m = re.search(r'-m (?:"([^"]+)"|(\S+))', command)
    return (m.group(1) or m.group(2)) if m else ""


def _lanes(target: str, rules) -> list[str]:
    prereqs, recipe = rules[target]
    lanes = [s for c in recipe if (s := _pytest_selection(c)) is not None]
    for p in prereqs:
        lanes += _lanes(p, rules)
    return lanes


def _selects(expr: str, markers: set[str]) -> bool:
    return not expr or Expression.compile(expr).evaluate(lambda n, **_: n in markers)


def test_the_done_line_runs_every_test_ci_runs():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    (ci_expr,) = [
        s for c in re.findall(r"run: (.*)", ci) if (s := _pytest_selection(c))
    ]
    rules = _makefile()
    targets = _done_targets()
    lanes = [lane for t in targets for lane in _lanes(t, rules)]
    missed = [
        kinds
        for n in range(len(MARKERS) + 1)
        for kinds in map(set, itertools.combinations(MARKERS, n))
        if _selects(ci_expr, kinds) and not any(_selects(e, kinds) for e in lanes)
    ]
    assert not missed, (
        f"CI runs tests marked {missed} that none of {targets} runs; "
        "name the target that does in AGENTS.md's done line"
    )
