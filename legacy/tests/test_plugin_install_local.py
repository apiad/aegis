"""install_plugin against a local-path source."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from aegis.plugins.install import InstallError, install_plugin


def _make_source(src: Path, *, name: str, with_install: bool = False) -> Path:
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        name = "{name}"
        version = "0.0.1"

        [default_config]
        folder = ".aegis/things/"
        k = 1
    """))
    (src / "code.py").write_text("# stub\n")
    if with_install:
        (src / "_install.py").write_text(textwrap.dedent("""
            from pathlib import Path
            def install(ctx):
                (ctx.aegis_dir / "things").mkdir(parents=True, exist_ok=True)
        """))
    return src


def _make_project(root: Path) -> Path:
    (root / ".aegis").mkdir(parents=True)
    (root / ".aegis.yaml").write_text("agents: {}\n")
    return root


def test_copy_and_lockfile(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "skill-system", name="skill-system")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="skill-system", source=src, project_root=proj, yes=True)
    installed = proj / ".aegis" / "plugins" / "skill-system"
    assert installed.is_dir()
    assert (installed / "plugin.toml").exists()
    assert (installed / "code.py").exists()
    lock = proj / ".aegis" / "plugins.lock"
    assert lock.exists()
    text = lock.read_text()
    assert "skill-system" in text
    assert "0.0.1" in text


def test_refuses_if_already_installed(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    with pytest.raises(InstallError, match="already installed"):
        install_plugin(name="x", source=src, project_root=proj, yes=True)


def test_force_reinstalls(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    install_plugin(name="x", source=src, project_root=proj, yes=True, force=True)


def test_install_py_runs(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x", with_install=True)
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    assert (proj / ".aegis" / "things").is_dir()


def test_config_merged(tmp_path: Path) -> None:
    src = _make_source(tmp_path / "src" / "x", name="x")
    proj = _make_project(tmp_path / "proj")
    install_plugin(name="x", source=src, project_root=proj, yes=True)
    yaml_text = (proj / ".aegis.yaml").read_text()
    assert "plugins:" in yaml_text
    assert "x:" in yaml_text
    assert ".aegis/things/" in yaml_text


def test_rollback_on_install_py_failure(tmp_path: Path) -> None:
    src = tmp_path / "src" / "bad"
    src.mkdir(parents=True)
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "bad"\nversion = "0.0.1"\n'
    )
    (src / "_install.py").write_text("def install(ctx):\n    raise RuntimeError('nope')\n")
    proj = _make_project(tmp_path / "proj")
    with pytest.raises(RuntimeError, match="nope"):
        install_plugin(name="bad", source=src, project_root=proj, yes=True)
    assert not (proj / ".aegis" / "plugins" / "bad").exists()
    lock = proj / ".aegis" / "plugins.lock"
    assert not lock.exists() or "bad" not in lock.read_text()
