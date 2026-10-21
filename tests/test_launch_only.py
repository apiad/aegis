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
# What starts a process, per module. A reference to one of these names is a
# hit even when it is not called on the spot: ``asyncio.to_thread(subprocess.run,
# ...)`` and ``functools.partial(subprocess.Popen, ...)`` start one later.
STARTERS = {
    "subprocess": SUBPROCESS,
    "asyncio": ASYNCIO,
    "asyncio.subprocess": ASYNCIO,
    "os": OS,
    "pty": {"spawn", "fork"},
    "webbrowser": {"open", "open_new", "open_new_tab", "get"},
    "concurrent.futures": {"ProcessPoolExecutor"},
}
# Modules whose import alone is a hit: everything in them runs in processes.
BANNED = ("multiprocessing", "concurrent.futures.process")
# The event loop's own spawns, on whatever loop object holds them.
LOOP = {"subprocess_exec", "subprocess_shell"}
DYNAMIC = {"importlib.import_module", "__import__"}


def _starts(module: str, name: str) -> bool:
    if module not in STARTERS:
        return False
    if name == "*":
        return True
    if module == "os" and name.startswith(OS_PREFIXES):
        return True
    return name in STARTERS[module]


def _banned(module: str) -> bool:
    return any(module == b or module.startswith(b + ".") for b in BANNED)


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Each name a module binds by import, to the dotted name it stands for."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    head = a.name.split(".")[0]
                    out[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _annotations(tree: ast.AST) -> set[int]:
    """The ids of every node inside a type annotation, which names a type
    and starts nothing."""
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            roots.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation:
            roots.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return {id(n) for r in roots for n in ast.walk(r)}


def _found(tree: ast.AST) -> list[tuple[int, str]]:
    aliases = _aliases(tree)
    skip = _annotations(tree)
    hits = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                if _banned(a.name):
                    hits.append((node.lineno, f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if _banned(node.module) or _banned(full):
                    hits.append((node.lineno, f"from {node.module} import {a.name}"))
                elif _starts(node.module, a.name):
                    hits.append((node.lineno, f"from {node.module} import {a.name}"))
        elif isinstance(node, ast.Attribute):
            if node.attr in LOOP:
                hits.append((node.lineno, f".{node.attr}"))
                continue
            name = _dotted(node, aliases)
            if name and "." in name:
                module, _, attr = name.rpartition(".")
                if _starts(module, attr):
                    hits.append((node.lineno, name))
        elif isinstance(node, ast.Call):
            func = (
                _dotted(node.func, aliases)
                if not isinstance(node.func, ast.Name)
                else node.func.id
            )
            first = node.args[0] if node.args else None
            if (
                func in DYNAMIC
                and (m := _string(first))
                and (m in STARTERS or _banned(m))
            ):
                hits.append((node.lineno, f"{func}({m!r})"))
            elif func == "getattr" and len(node.args) >= 2 and first is not None:
                module = _dotted(first, aliases)
                attr = _string(node.args[1])
                if module and attr and _starts(module, attr):
                    hits.append((node.lineno, f"getattr({module}, {attr!r})"))
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


BYPASSES = {
    "from_import": "from subprocess import run\nrun(['true'])\n",
    "module_alias": "import subprocess as sp\nsp.run(['true'])\n",
    "asyncio_subprocess": "import asyncio\nasyncio.subprocess.create_subprocess_exec('x')\n",
    "to_thread": "import asyncio, subprocess\nasyncio.to_thread(subprocess.run, ['x'])\n",
    "executor": "import subprocess\nloop.run_in_executor(None, subprocess.run, ['x'])\n",
    "partial": "import functools, subprocess\nfunctools.partial(subprocess.Popen, ['x'])\n",
    "loop_exec": "loop.subprocess_exec(proto, 'x')\n",
    "loop_shell": "loop.subprocess_shell(proto, 'x')\n",
    "os_alias": "import os as o\no.execvp('x', ['x'])\n",
    "asyncio_alias": "import asyncio as aio\naio.create_subprocess_exec('x')\n",
    "pty_alias": "import pty as p\np.spawn('x')\n",
    "asp_alias": "import asyncio.subprocess as asp\nasp.create_subprocess_exec('x')\n",
    "from_asyncio_subprocess": "from asyncio import subprocess\nsubprocess.create_subprocess_exec('x')\n",
    "star": "from subprocess import *\n",
    "getattr": "import subprocess\ngetattr(subprocess, 'run')(['x'])\n",
    "import_module": "import importlib\nimportlib.import_module('subprocess')\n",
    "dunder_import": "__import__('subprocess')\n",
    "multiprocessing": "import multiprocessing\n",
    "process_pool": "from concurrent.futures import ProcessPoolExecutor\n",
    "webbrowser": "import webbrowser\nwebbrowser.open('http://x')\n",
}


def test_every_way_around_launch_is_caught(tmp_path):
    for name, code in BYPASSES.items():
        (tmp_path / f"{name}.py").write_text(code)
    found = offenders(tmp_path)
    assert {n for n in BYPASSES if f"{n}.py" not in found} == set()


def test_what_only_names_process_types_is_allowed(tmp_path):
    (tmp_path / "ok.py").write_text(
        "import asyncio, os, subprocess\n"
        "from asyncio.subprocess import PIPE, STDOUT\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "p: asyncio.subprocess.Process\n"
        "def f(a: subprocess.Popen) -> subprocess.Popen: ...\n"
        "e = subprocess.TimeoutExpired\n"
        "x = subprocess.DEVNULL, os.environ, os.kill, os.getpid()\n"
        "asyncio.to_thread(len, [])\n"
    )
    assert offenders(tmp_path) == {}
