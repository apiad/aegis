"""InstallContext field shape + helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.plugins.install_context import InstallContext


def _ctx(tmp_path: Path, **overrides) -> InstallContext:
    defaults = dict(
        project_root=tmp_path,
        aegis_dir=tmp_path / ".aegis",
        plugin_dir=tmp_path / ".aegis/plugins/foo",
        plugin_name="foo",
        manifest={"plugin": {"name": "foo", "version": "0.1"}},
        config=None,
        console=None,
        _confirm_default=True,
        _yes=False,
    )
    defaults.update(overrides)
    return InstallContext(**defaults)


def test_paths_exposed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert ctx.project_root == tmp_path
    assert ctx.aegis_dir == tmp_path / ".aegis"
    assert ctx.plugin_dir == tmp_path / ".aegis/plugins/foo"
    assert ctx.plugin_name == "foo"


def test_confirm_default_when_yes_flag(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _yes=True)
    assert ctx.confirm("anything?", default=True) is True
    assert ctx.confirm("anything?", default=False) is False
