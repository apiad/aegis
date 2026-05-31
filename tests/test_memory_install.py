"""Hermetic tests for memory-system install / uninstall."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from aegis.plugins.install_context import InstallContext


def _load_module(name: str):
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_test_{name}"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(tmp_path: Path, *, yes: bool) -> InstallContext:
    yaml_path = tmp_path / ".aegis.yaml"
    if not yaml_path.exists():
        yaml_path.write_text("", encoding="utf-8")
    return InstallContext(
        project_root=tmp_path,
        aegis_dir=tmp_path,
        plugin_dir=tmp_path / "plugins" / "memory-system",
        plugin_name="memory-system",
        manifest={"plugin": {"name": "memory-system", "version": "0.1.0"},
                  "default_config": {"lookback_days": 7,
                                     "max_session_files": 50,
                                     "dreamer_agent": "dreamer"}},
        config=None,
        console=None,
        _confirm_default=True,
        _yes=yes,
    )


def test_install_creates_directory_tree(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/entries").is_dir()
    assert (tmp_path / ".aegis/memory/dreams").is_dir()


def test_install_writes_stub_files(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/SOUL.md").exists()
    assert (tmp_path / ".aegis/memory/USER.md").exists()
    assert (tmp_path / ".aegis/memory/MEMORY.md").exists()


def test_install_preserves_existing_files(tmp_path: Path) -> None:
    install = _load_module("_install")
    (tmp_path / ".aegis/memory").mkdir(parents=True)
    (tmp_path / ".aegis/memory/SOUL.md").write_text("MINE\n", encoding="utf-8")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/SOUL.md").read_text() == "MINE\n"


def test_install_adds_dreamer_agent_and_memory_block(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    text = (tmp_path / ".aegis.yaml").read_text(encoding="utf-8")
    assert "dreamer:" in text
    assert "memory:" in text
    assert "lookback_days: 7" in text


def test_install_writes_schedule_overlay_when_yes(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    overlay = tmp_path / ".aegis/schedules/memory-dream.yaml"
    assert overlay.exists()
    body = overlay.read_text(encoding="utf-8")
    assert "workflow: dream" in body
    assert "0 3 * * *" in body
