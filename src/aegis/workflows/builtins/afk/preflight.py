"""Refusing to dispatch a worker into a checkout that is not ready.

The gate command is resolved from the repo's own Makefile rather than
configured per repo, because the repo is the thing that knows, and a gate
named in config drifts from the gate that exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

Bash = Callable[[str, str], Awaitable[dict]]

NO_GATE = "none"


@dataclass(frozen=True)
class Preflight:
    ok: bool
    reason: str
    gate_cmd: str = NO_GATE
    branch: str = ""


def resolve_gate(makefile_text: str, gate_commands: tuple[str, ...]) -> str:
    """The first of `gate_commands` whose target the Makefile actually
    declares, or "none".

    A `.PHONY: check` line declares nothing runnable, and a target inside a
    comment declares nothing at all, so the match is on a rule at the start
    of a line.
    """
    for cmd in gate_commands:
        target = cmd.split()[-1]
        rule = re.compile(rf"^{re.escape(target)}\s*:", re.M)
        for line in (makefile_text or "").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if line.startswith(".PHONY"):
                continue
            if rule.match(line):
                return cmd
    return NO_GATE


async def preflight(
    bash: Bash, repo_path: Path, *, gate_commands: tuple[str, ...]
) -> Preflight:
    cwd = str(repo_path)

    fetched = await bash("git fetch --prune --quiet", cwd)
    if fetched["exit"] != 0:
        return Preflight(False, f"git fetch failed: {fetched['stdout'].strip()[:300]}")

    branch_res = await bash("git rev-parse --abbrev-ref HEAD", cwd)
    if branch_res["exit"] != 0:
        return Preflight(False, "could not read the current branch")
    branch = branch_res["stdout"].strip()

    pulled = await bash("git pull --ff-only --quiet", cwd)
    if pulled["exit"] != 0:
        return Preflight(
            False,
            f"git pull --ff-only failed: {pulled['stdout'].strip()[:300]}",
            branch=branch,
        )

    # After the pull, never before: a pull can leave conflict markers, and a
    # tree checked before it would read clean.
    dirty = await bash("git status --porcelain", cwd)
    if dirty["exit"] != 0:
        return Preflight(False, "could not read the working tree", branch=branch)
    # Split the raw output, not a stripped copy: porcelain's first two columns
    # are the status and either may be a space, so stripping eats the path.
    entries = [ln for ln in dirty["stdout"].splitlines() if ln.strip()]
    if entries:
        paths = ", ".join(ln[3:] for ln in entries[:10])
        return Preflight(False, f"working tree is dirty: {paths}", branch=branch)

    makefile = repo_path / "Makefile"
    text = (
        makefile.read_text(encoding="utf-8", errors="replace")
        if makefile.is_file()
        else ""
    )
    return Preflight(
        True, "ready", gate_cmd=resolve_gate(text, gate_commands), branch=branch
    )
