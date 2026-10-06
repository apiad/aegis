"""Three roots, never the process's working directory.

The CLI reads it once to build the roots and passes them down. Anything below
the CLI that calls ``Path.cwd()`` or ``os.getcwd()`` resolves against whatever
directory the server happened to start in.
"""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "aegis"


def _cwd_calls(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in ("cwd", "getcwd"):
            lines.append(node.lineno)
        elif isinstance(fn, ast.Name) and fn.id == "getcwd":
            lines.append(node.lineno)
    return lines


def test_only_the_cli_reads_the_working_directory():
    files = sorted(PKG.rglob("*.py"))
    assert files
    for f in files:
        if f.name == "cli.py" and f.parent == PKG:
            continue
        assert not _cwd_calls(f), (
            f"{f.relative_to(PKG)} reads the cwd at lines {_cwd_calls(f)}"
        )
