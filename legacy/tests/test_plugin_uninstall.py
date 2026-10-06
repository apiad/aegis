"""uninstall_plugin: run _uninstall.py, delete folder, strip config,
leave user data alone."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aegis.plugins.install import install_plugin
from aegis.plugins.uninstall import UninstallError, uninstall_plugin


def _setup_installed(tmp_path: Path, *, with_uninstall: bool = False) -> Path:
    src = tmp_path / "src" / "x"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(textwrap.dedent("""
        [plugin]
        name = "x"
        version = "0.1"
        [default_config]
        k = 1
    """))
    (src / "code.py").write_text("# stub\n")
    if with_uninstall:
        (src / "_uninstall.py").write_text(textwrap.dedent("""
            def uninstall(ctx):
                (ctx.aegis_dir / "x-teardown").write_text("done")
        """))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("plugins: {}\n")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    return proj


def test_deletes_folder(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "x").exists()


def test_strips_config_section(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    yaml_text = (proj / ".aegis.yaml").read_text()
    assert "x:" not in yaml_text or "plugins" not in yaml_text


def test_uninstall_py_runs(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path, with_uninstall=True)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert (proj / ".aegis" / "x-teardown").exists()


def test_leaves_user_data_alone(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    user_data = proj / ".aegis" / "user-thing"
    user_data.mkdir()
    (user_data / "important.txt").write_text("keep me")
    uninstall_plugin(name="x", project_root=proj, yes=True)
    assert (user_data / "important.txt").read_text() == "keep me"


def test_lockfile_entry_removed(tmp_path: Path) -> None:
    proj = _setup_installed(tmp_path)
    uninstall_plugin(name="x", project_root=proj, yes=True)
    lock = (proj / ".aegis" / "plugins.lock").read_text() \
        if (proj / ".aegis" / "plugins.lock").exists() else ""
    assert "name = \"x\"" not in lock and "name = 'x'" not in lock


def test_uninstall_py_exception_logged_and_continued(tmp_path: Path) -> None:
    src = tmp_path / "src" / "y"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text('[plugin]\nname = "y"\nversion = "0.1"\n')
    (src / "_uninstall.py").write_text(
        "def uninstall(ctx):\n    raise RuntimeError('boom')\n"
    )
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("plugins: {}\n")
    install_plugin(name="y", source=src, project_root=proj, yes=True)
    # Should not raise — uninstall log-and-continues.
    uninstall_plugin(name="y", project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "y").exists()


def test_uninstalling_unknown_plugin_errors(tmp_path: Path) -> None:
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("plugins: {}\n")
    with pytest.raises(UninstallError, match="not installed"):
        uninstall_plugin(name="never", project_root=proj, yes=True)
