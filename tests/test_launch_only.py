"""No module but launch.py starts a process (#548).

The multi-user mode puts the user switch and the capability drop in
launch.py; a process started anywhere else would skip both. This reads every
module under src/aegis with ``ast`` and names each call that starts a process,
and each import that would hide one under another name.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "aegis"

SUBPROCESS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
}
ASYNCIO = {"create_subprocess_exec", "create_subprocess_shell"}
OS = {"system", "popen", "posix_spawn", "posix_spawnp", "fork", "forkpty"}
OS_PREFIXES = ("exec", "spawn")
PTY = {"spawn", "fork"}


def _starts(module: str, name: str) -> bool:
    if module == "subprocess":
        return name in SUBPROCESS
    if module in ("asyncio", "asyncio.subprocess"):
        return name in ASYNCIO
    if module == "os":
        return name in OS or name.startswith(OS_PREFIXES)
    if module == "pty":
        return name in PTY
    return False


def _dotted(node: ast.expr) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _found(tree: ast.AST) -> list[tuple[int, str]]:
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess" and a.asname not in (None, "subprocess"):
                    hits.append((node.lineno, f"import subprocess as {a.asname}"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if _starts(node.module, a.name):
                    hits.append((node.lineno, f"from {node.module} import {a.name}"))
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name and "." in name:
                module, _, attr = name.rpartition(".")
                if _starts(module, attr):
                    hits.append((node.lineno, name))
    return hits


def offenders(root: Path = SRC) -> dict[str, list[tuple[int, str]]]:
    """``{path relative to root: [(line, what)]}`` outside launch.py."""
    out = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "launch.py":
            continue
        hits = _found(ast.parse(path.read_text(), str(path)))
        if hits:
            out[rel] = hits
    return out


def test_no_process_starts_outside_launch():
    assert offenders() == {}


def test_an_aliased_import_is_caught(tmp_path):
    (tmp_path / "a.py").write_text("from subprocess import run\nrun(['true'])\n")
    (tmp_path / "b.py").write_text("import subprocess as sp\nsp.run(['true'])\n")
    (tmp_path / "c.py").write_text(
        "import asyncio\nasyncio.subprocess.create_subprocess_exec('x')\n"
    )
    found = offenders(tmp_path)
    assert set(found) == {"a.py", "b.py", "c.py"}
