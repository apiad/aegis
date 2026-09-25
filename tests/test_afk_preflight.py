"""Refusing to dispatch into a checkout that is not ready."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.workflows.builtins.afk.preflight import preflight, resolve_gate

MAKEFILE = """
.PHONY: check test fmt

check: fmt test
\t@echo ok

test:
\tuv run pytest
"""


def test_resolve_gate_picks_the_first_declared_target() -> None:
    assert resolve_gate(MAKEFILE, ("make check", "make test")) == "make check"


def test_resolve_gate_falls_through_to_the_second() -> None:
    assert resolve_gate("test:\n\tpytest\n", ("make check", "make test")) == "make test"


def test_resolve_gate_returns_none_when_no_target_matches() -> None:
    """A repo with no gate still runs, but its report must not read as a
    green gate. 'none' is a value, not a silent success."""
    assert resolve_gate("build:\n\tcc x.c\n", ("make check", "make test")) == "none"


def test_resolve_gate_ignores_a_target_named_in_a_comment() -> None:
    assert resolve_gate("# check: not a real target\n", ("make check",)) == "none"


def test_resolve_gate_ignores_a_phony_declaration_alone() -> None:
    """`.PHONY: check` without a `check:` rule declares nothing runnable."""
    assert resolve_gate(".PHONY: check\n", ("make check",)) == "none"


def test_resolve_gate_handles_an_absent_makefile() -> None:
    assert resolve_gate("", ("make check",)) == "none"


class FakeBash:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def __call__(self, cmd: str, cwd: str) -> dict:
        self.calls.append(cmd)
        for needle, res in self.results.items():
            if needle in cmd:
                return res
        return {"exit": 0, "stdout": ""}


@pytest.mark.asyncio
async def test_preflight_passes_on_a_clean_tree(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(MAKEFILE)
    bash = FakeBash({
        "status --porcelain": {"exit": 0, "stdout": ""},
        "rev-parse --abbrev-ref": {"exit": 0, "stdout": "main\n"},
    })
    out = await preflight(bash, tmp_path, gate_commands=("make check", "make test"))
    assert out.ok is True
    assert out.gate_cmd == "make check"
    assert out.branch == "main"


@pytest.mark.asyncio
async def test_preflight_refuses_a_dirty_tree_and_names_the_paths(tmp_path) -> None:
    """A worker building on somebody else's half-landed change produces a
    diff nobody can review, and on a shared checkout that somebody is often
    the operator."""
    (tmp_path / "Makefile").write_text(MAKEFILE)
    bash = FakeBash({
        "status --porcelain": {"exit": 0, "stdout": " M src/a.py\n?? scratch.txt\n"},
        "rev-parse --abbrev-ref": {"exit": 0, "stdout": "main\n"},
    })
    out = await preflight(bash, tmp_path, gate_commands=("make check",))
    assert out.ok is False
    assert "src/a.py" in out.reason


@pytest.mark.asyncio
async def test_preflight_refuses_when_fetch_fails(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(MAKEFILE)
    bash = FakeBash({"fetch": {"exit": 128, "stdout": "could not resolve host"}})
    out = await preflight(bash, tmp_path, gate_commands=("make check",))
    assert out.ok is False
    assert "fetch" in out.reason


@pytest.mark.asyncio
async def test_preflight_refuses_when_pull_is_not_fast_forward(tmp_path) -> None:
    """A non-fast-forward means local commits nobody has looked at. Merging
    them is not the coordinator's call."""
    (tmp_path / "Makefile").write_text(MAKEFILE)
    bash = FakeBash({
        "status --porcelain": {"exit": 0, "stdout": ""},
        "rev-parse --abbrev-ref": {"exit": 0, "stdout": "main\n"},
        "pull --ff-only": {"exit": 1, "stdout": "not possible to fast-forward"},
    })
    out = await preflight(bash, tmp_path, gate_commands=("make check",))
    assert out.ok is False
    assert "fast-forward" in out.reason


@pytest.mark.asyncio
async def test_preflight_checks_the_tree_after_pulling(tmp_path) -> None:
    """Order matters: a pull can leave conflict markers, so the dirty check
    has to come after it, not before."""
    (tmp_path / "Makefile").write_text(MAKEFILE)
    bash = FakeBash({
        "status --porcelain": {"exit": 0, "stdout": ""},
        "rev-parse --abbrev-ref": {"exit": 0, "stdout": "main\n"},
    })
    await preflight(bash, tmp_path, gate_commands=("make check",))
    pull_at = next(i for i, c in enumerate(bash.calls) if "pull --ff-only" in c)
    status_at = next(i for i, c in enumerate(bash.calls) if "status --porcelain" in c)
    assert pull_at < status_at
