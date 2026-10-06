"""aegis2 is built next to the old tree, never on top of it.

No module under ``src/aegis2/`` imports ``aegis``: code that is already right is
copied and adapted. Imports inside aegis2 are relative, so the rename to
``aegis`` at the switch is one mechanical commit.
"""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "src" / "aegis2"


def _absolute_imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_aegis2_never_imports_the_old_tree_or_itself_absolutely():
    files = sorted(PKG.rglob("*.py"))
    assert files, "found no modules to check"
    for f in files:
        bad = [
            n for n in _absolute_imports(f) if n.split(".")[0] in ("aegis", "aegis2")
        ]
        assert not bad, f"{f.relative_to(PKG)} imports {bad}"
