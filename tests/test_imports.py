"""Imports inside aegis are relative, and nothing imports the legacy tree.

Relative imports kept the tree movable: it was built as a second package next
to the old one, and taking over the ``aegis`` name was one directory move. The legacy tree under
``legacy/`` is reference only; it is not packaged, so an import of it would
work on a checkout and fail on an install.
"""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "aegis"


def _absolute_imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_aegis_imports_itself_relatively_and_never_the_legacy_tree():
    files = sorted(PKG.rglob("*.py"))
    assert files, "found no modules to check"
    for f in files:
        bad = [
            n
            for n in _absolute_imports(f)
            if n.split(".")[0] in ("aegis", "aegis", "legacy")
        ]
        assert not bad, f"{f.relative_to(PKG)} imports {bad}"
