"""Structural guard. The 66 cwd sites are being removed module by module;
this pins the ones already done so they cannot regress. Add modules to
CLEANED as later tasks finish them."""
from pathlib import Path

CLEANED = [
    "core/manager.py",
    "core/session.py",
    "mcp/server.py",
    "terminal/manager.py",
    "workflow/engine.py",
    "usage/env.py",
]


def test_cleaned_modules_do_not_resolve_from_the_process_cwd():
    # Anchored on this file, not the process cwd: the autouse
    # ``isolated_project_dir`` fixture chdirs every test to a tmp dir, so a
    # relative path here reads nothing and the guard errors instead of
    # reporting offenders.
    src = Path(__file__).resolve().parents[1] / "src" / "aegis"
    offenders = []
    for rel in CLEANED:
        text = (src / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "Path.cwd()" in line or "os.getcwd()" in line:
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, "cwd resolution reintroduced:\n" + "\n".join(offenders)
