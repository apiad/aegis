"""Every file access in the modules that serve a person's files is accounted
for (#555): it runs on the runner of the person's place (as_user.py), or it
touches only the server's own state. A new one fails here until it is put in
one of the two tables, which is the moment to decide which it is."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "aegis"
MODULES = (
    "fs_ops.py",
    "fs_index.py",
    "fs_trash.py",
    "files.py",
    "attachments.py",
    "artifacts.py",
    "artifact_ops.py",
    "web.py",
)
CALLS = {"open", "os.open", "os.scandir", "os.listdir", "os.walk", "os.stat"}
METHODS = {
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "open",
    "iterdir",
    "mkdir",
    "unlink",
    "rename",
    "replace",
    "exists",
    "is_file",
    "is_dir",
    "stat",
    "glob",
    "rglob",
}

# Calls whose method name matches but that touch no file.
NOT_FILES = {"os.replace", "re.compile", "shutil.move", "reg.open"}

# Functions run on a place's runner, with the person's access, in the mode.
ON_RUNNER = {
    "fs_ops.py": {
        "_hash",
        "_kind",
        "_read",
        "_write",
        "_save",
        "_repo_top",
        "_holds_repo",
        "_list",
        "_entry",
        "_file",
        "_stat",
        "_folder",
        "_mkdir",
        "_move",
        "_restore",
        "read.<lambda>",
        "write.save",
        "count.<lambda>",
        "delete.delete",
    },
    "fs_index.py": {
        "_is_repo",
        "_Walk.keep",
        "_Walk.tree",
        "_Walk.search",
        "walk",
        "FsIndex.touched",
        "FsIndex._scan",
        "FsIndex.added",
    },
    "fs_trash.py": {"count", "delete", "manifest", "restore", "purge"},
    "files.py": {"_sniff_text", "check", "open_all", "excerpt"},
    "attachments.py": {"ensure_inbox", "write_into", "_free"},
    "artifacts.py": {"write_draft", "drop_draft"},
    "web.py": {"_open_as"},
}

# Functions that touch only the server's state, or run outside the mode.
SERVER = {
    "fs_ops.py": {"register_fs_ops.log", "open_"},
    "files.py": {"store", "store_all", "find", "open_natively", "headers", "classify"},
    "attachments.py": {
        "_staged",
        "_file",
        "begin",
        "put",
        "drop",
        "commit",
        "_move",
        "clear_staged",
    },
    "artifacts.py": {"Artifacts._write_state"},
    # The client's own assets ship with aegis.
    "web.py": {
        "_file_response",
        "sent_file",
        "fs_file",
        "dictation_file",
        "service_worker",
        "client_files",
        "index",
    },
}


def _name(call: ast.Call) -> tuple[str | None, str | None]:
    """(dotted call name, method name) of a call."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id, None
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name):
            return f"{f.value.id}.{f.attr}", f.attr
        return None, f.attr
    return None, None


def sites(module: str) -> set[str]:
    """The enclosing functions, as dotted names, of every file access."""
    tree = ast.parse((SRC / module).read_text())
    found: set[str] = set()

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*scope, child.name])
            elif isinstance(child, ast.Lambda):
                visit(child, [*scope, "<lambda>"])
            else:
                if isinstance(child, ast.Call):
                    dotted, method = _name(child)
                    if dotted in CALLS or (
                        method in METHODS and dotted not in NOT_FILES
                    ):
                        found.add(".".join(scope[-2:]) if scope else "<module>")
                visit(child, scope)

    visit(tree, [])
    return found


def test_every_file_access_is_accounted_for():
    def known(m: str, site: str) -> bool:
        names = ON_RUNNER.get(m, set()) | SERVER.get(m, set())
        return site in names or site.split(".")[-1] in names

    unknown = {m: sorted(s for s in sites(m) if not known(m, s)) for m in MODULES}
    assert {m: s for m, s in unknown.items() if s} == {}
