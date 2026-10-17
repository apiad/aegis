"""Every token-bench task can pass and cannot pass untouched, with no model.

For each folder in bench/tasks/: setup.sh on a fresh WORK, then tests/test.sh, must
fail; setup.sh, then any solution/*.sh, then tests/test.sh, must pass. A task can
list the host tools it needs in task.toml's `requires`; a missing one skips it.
The virtualenv's bin directory goes first on PATH so `python3 -m pytest` in a
task script has pytest, here and in CI.
"""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

TASKS = Path(__file__).resolve().parents[1] / "bench" / "tasks"
TASK_DIRS = sorted(p for p in TASKS.iterdir() if p.is_dir())
SOLUTIONS = [(t, s) for t in TASK_DIRS for s in sorted((t / "solution").glob("*.sh"))]
ENV = {
    **os.environ,
    "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
}


def _toml(task: Path) -> dict:
    return tomllib.loads((task / "task.toml").read_text())


def _run(script: Path, work: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), str(work)],
        env=ENV,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _setup(task: Path, tmp_path: Path) -> Path:
    missing = [t for t in _toml(task).get("requires", []) if shutil.which(t) is None]
    if missing:
        pytest.skip(f"{task.name} needs {', '.join(missing)} on PATH")
    work = tmp_path / "work"
    work.mkdir()
    r = _run(task / "setup.sh", work)
    assert r.returncode == 0, r.stderr
    return work


def _last_line(r: subprocess.CompletedProcess) -> str:
    lines = r.stdout.strip().splitlines()
    return lines[-1] if lines else ""


@pytest.mark.parametrize("task", TASK_DIRS, ids=lambda p: p.name)
def test_the_untouched_start_fails_its_check(task, tmp_path):
    work = _setup(task, tmp_path)
    r = _run(task / "tests" / "test.sh", work)
    assert r.returncode != 0 and _last_line(r).startswith("FAIL: "), r.stdout + r.stderr


@pytest.mark.parametrize(
    ("task", "solution"), SOLUTIONS, ids=[f"{t.name}-{s.stem}" for t, s in SOLUTIONS]
)
def test_every_solution_passes_its_check(task, solution, tmp_path):
    work = _setup(task, tmp_path)
    r = _run(solution, work)
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(task / "tests" / "test.sh", work)
    assert r.returncode == 0 and _last_line(r) == "PASS", r.stdout + r.stderr


@pytest.mark.parametrize("task", TASK_DIRS, ids=lambda p: p.name)
def test_task_toml_names_its_band(task):
    meta = _toml(task)
    assert 15 <= meta["expert_minutes"] <= 45
    assert meta["timeout_s"] > 0
    assert meta["category"]
    assert (task / "instruction.md").read_text().strip()
    assert list((task / "solution").glob("*.sh"))


# A command word at a command position: line start, or after $( && || | ; or !.
_UNTIMED = re.compile(r"(?:^|\$\(|&&|\|\||[|;!])\s*(python3|pytest|make|\./)", re.M)


@pytest.mark.parametrize("task", TASK_DIRS, ids=lambda p: p.name)
def test_every_check_command_runs_under_a_timeout(task):
    """A seeded bug can hang, so no check command may run without `timeout`."""
    text = (task / "tests" / "test.sh").read_text()
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert not [m.group(0).strip() for m in _UNTIMED.finditer(code)]
