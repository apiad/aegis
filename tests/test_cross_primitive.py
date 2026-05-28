"""A fixture plugin with @workflow + @hook + @tool in one file —
all three primitives must register and be reachable."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.config.yaml_loader import AegisConfig, import_plugins
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG, _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _REGISTRY as _TOOL_REG, _reset_registry_for_tests as _reset_tools
from aegis.workflow import REGISTRY as _WORKFLOW_REG


@pytest.fixture(autouse=True)
def _clean():
    _reset_hooks()
    _reset_tools()
    snapshot = dict(_WORKFLOW_REG)
    yield
    _reset_hooks()
    _reset_tools()
    _WORKFLOW_REG.clear()
    _WORKFLOW_REG.update(snapshot)


def test_one_file_registers_all_three(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "kitchen-sink"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(
        '[plugin]\nname = "kitchen-sink"\nversion = "0.0.1"\n'
    )
    (plug / "mod.py").write_text(textwrap.dedent("""
        from aegis.hooks import hook, PreTurnResult
        from aegis.tools import tool
        from aegis.workflow import workflow

        @hook("pre_turn")
        async def my_hook(ctx):
            return PreTurnResult(prepend_system="hi")

        @tool
        async def my_tool(x: int) -> int:
            \"\"\"Doubles x.\"\"\"
            return x * 2

        @workflow
        async def my_wf(engine):
            return "ok"
    """))

    import_plugins(AegisConfig(plugin_dirs=[plug.parent]))

    assert len(_HOOK_REG["pre_turn"]) == 1
    assert "my_tool" in _TOOL_REG
    assert "my_wf" in _WORKFLOW_REG
