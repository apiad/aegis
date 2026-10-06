"""`aegis web` relays frames; it must not learn what flows through them.

Checked on the import graph, because that is where the retired web layer's
knowledge arrived: it held the manager and called eleven of its methods.
"""

import ast
from pathlib import Path

import aegis.webterm

FORBIDDEN = ("aegis.core", "aegis.tui", "aegis.views", "aegis.web.", "aegis.mcp")
PKG = Path(aegis.webterm.__file__).parent


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_aegis_web_knows_no_aegis_concepts():
    files = sorted(PKG.glob("*.py"))
    assert files, "found no modules to check"
    for f in files:
        bad = sorted(
            n
            for n in _imports(f)
            if (n + ".").startswith(FORBIDDEN) or n == "aegis.web"
        )
        assert not bad, f"{f.name} imports {bad}"
