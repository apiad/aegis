"""Structural guard. The 66 cwd sites are being removed module by module;
this pins the ones already done so they cannot regress. Add modules to
CLEANED as later tasks finish them.

By AST rather than substring, for the reason Task 4's guard already gives:
a text scan is fooled by a line break and fires on a mention in a comment.
Both bit the substring version of this guard on the day it was written —
``Path.cwd(\\n)`` slipped past it (verified by mutation), and a comment in
``core/manager.py`` describing the bug being removed was reported as an
offender, which cost a reword of prose that was correct. Assert on calls,
not on text.
"""
import ast
from pathlib import Path

CLEANED = [
    # ``<module>::<function>`` narrows the guard to one function. ``cli.py``
    # needs it: its typer entrypoints legitimately read the process cwd —
    # that is the CLI's input — so only the boot path is cleaned.
    "cli.py::_serve",
    "core/manager.py",
    "core/session.py",
    "mcp/server.py",
    "terminal/manager.py",
    "workflow/engine.py",
    "usage/env.py",
]


def _cwd_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """``Path.cwd()`` and ``os.getcwd()`` calls, however they are formatted.

    Matches on the attribute name against its receiver, so the whitespace
    inside the call is irrelevant and a docstring naming ``Path.cwd()``
    is not a hit.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        recv = fn.value
        recv_name = recv.id if isinstance(recv, ast.Name) else None
        if fn.attr == "cwd" and recv_name == "Path":
            found.append((node.lineno, "Path.cwd()"))
        elif fn.attr == "getcwd" and recv_name == "os":
            found.append((node.lineno, "os.getcwd()"))
    return found


def _function_named(tree: ast.AST, name: str, entry: str) -> ast.AST:
    """The named top-level function, or a hard failure.

    Raising beats returning an empty tree: a renamed function would
    otherwise make the guard silently pass on nothing at all.
    """
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            return node
    raise AssertionError(f"{entry}: no function named {name!r}")


def test_cleaned_modules_do_not_resolve_from_the_process_cwd():
    # Anchored on this file, not the process cwd: the autouse
    # ``isolated_project_dir`` fixture chdirs every test to a tmp dir, so a
    # relative path here reads nothing and the guard errors instead of
    # reporting offenders.
    src = Path(__file__).resolve().parents[1] / "src" / "aegis"
    offenders = []
    for entry in CLEANED:
        rel, _, func = entry.partition("::")
        tree = ast.parse((src / rel).read_text(encoding="utf-8"))
        if func:
            tree = _function_named(tree, func, entry)
        offenders += [f"{entry}:{line}: {what}" for line, what in _cwd_calls(tree)]
    assert not offenders, "cwd resolution reintroduced:\n" + "\n".join(offenders)
