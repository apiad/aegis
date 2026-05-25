"""Tests for plugin auto-import from .aegis/plugins/*.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import import_plugins, load_config


def test_plugin_registers_workflow(tmp_path: Path) -> None:
    from aegis.workflow import REGISTRY
    REGISTRY.pop("my_test_wf", None)

    (tmp_path / ".aegis.yaml").write_text("")
    plugins = tmp_path / ".aegis" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "myhook.py").write_text(
        "from aegis.workflow import workflow\n"
        "@workflow\nasync def my_test_wf(engine): return 'ok'\n"
    )
    cfg = load_config(tmp_path)
    import_plugins(cfg)
    assert "my_test_wf" in REGISTRY


def test_plugin_import_error_fails(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    plugins = tmp_path / ".aegis" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "broken.py").write_text("import not_a_real_module\n")
    cfg = load_config(tmp_path)
    with pytest.raises(ModuleNotFoundError):
        import_plugins(cfg)


def test_plugin_dir_missing_is_ok(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("")
    cfg = load_config(tmp_path)
    import_plugins(cfg)


def test_register_builtins_unknown_fails(tmp_path: Path) -> None:
    from aegis.config.yaml_loader import register_builtins
    (tmp_path / ".aegis.yaml").write_text("workflows: [not_a_real_workflow]\n")
    cfg = load_config(tmp_path)
    with pytest.raises(ConfigError, match="unknown built-in"):
        register_builtins(cfg)
