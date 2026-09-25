"""The `workflows:` config key must actually register its built-ins.

Regression test for a bug that made every built-in workflow unreachable from
config: `register_builtins` existed, was unit-tested directly, and was called
by no boot path. A schedule naming a built-in fired into an empty registry
with `unknown workflow: 'afk'. Available: []`, five times, silently, in a
JSONL nobody was tailing.

These tests go through the boot path rather than calling the loader function,
because calling the function was exactly what passed while the product was
broken.
"""

from __future__ import annotations

import textwrap

import pytest

from aegis.config import find_project_root  # noqa: F401  (import sanity)


def _write_root(tmp_path, body: str):
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent(body))
    return tmp_path


def test_resolve_boot_registers_a_builtin_named_in_workflows(tmp_path) -> None:
    """`resolve_boot` is what `aegis serve` runs. If the registry is empty
    after it, every scheduled built-in is dead on arrival."""
    from aegis.config.yaml_loader import load_workflow_registry, load_config

    root = _write_root(
        tmp_path,
        """
        agents:
          opus:
            provider: claude-code
            model: opus
        default_agent: opus
        workflows:
          - afk
        """,
    )
    load_workflow_registry(load_config(root))

    from aegis.workflow import get_workflow

    assert get_workflow("afk") is not None
    assert get_workflow("afk_progress") is not None


def test_an_unknown_builtin_name_fails_loud(tmp_path) -> None:
    from aegis.config import ConfigError
    from aegis.config.yaml_loader import load_workflow_registry, load_config

    root = _write_root(
        tmp_path,
        """
        agents:
          opus:
            provider: claude-code
            model: opus
        default_agent: opus
        workflows:
          - no_such_builtin
        """,
    )
    with pytest.raises(ConfigError, match="unknown built-in"):
        load_workflow_registry(load_config(root))


def test_no_boot_path_calls_import_plugins_without_the_builtins(tmp_path) -> None:
    """The bug was two functions that had to be called together, and every
    caller calling only one. Nothing outside the loader may call either half
    directly — the single entry point is the fix, and this is what keeps it.
    """
    import pathlib
    import re

    src = pathlib.Path("src/aegis")
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name == "yaml_loader.py":
            continue  # defines both halves and the entry point
        text = path.read_text(encoding="utf-8")
        for half in ("import_plugins", "register_builtins"):
            if re.search(rf"\b{half}\(", text):
                offenders.append(f"{path}: {half}()")
    assert offenders == [], (
        "call load_workflow_registry() instead: " + ", ".join(offenders)
    )
