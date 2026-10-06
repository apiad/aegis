"""Tests for the plugin auto-import recursion + underscore-skip rules."""
from __future__ import annotations

import textwrap
from pathlib import Path

from aegis.config.yaml_loader import AegisConfig, import_plugins


def _make_cfg(plugin_dir: Path) -> AegisConfig:
    return AegisConfig(plugin_dirs=[plugin_dir])


def test_recurses_into_subfolders(tmp_path: Path) -> None:
    plug = tmp_path / "plugins"
    sub = plug / "skill-system" / "nested"
    sub.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (sub / "deep.py").write_text(
        textwrap.dedent(f"""
        from pathlib import Path
        Path({str(marker)!r}).write_text("loaded")
        """)
    )
    import_plugins(_make_cfg(plug))
    assert marker.read_text() == "loaded"


def test_skips_underscore_prefixed_files(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "my-plugin"
    plug.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (plug / "_install.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"
    )
    import_plugins(_make_cfg(plug.parent))
    assert not marker.exists(), "_install.py must not be auto-imported"


def test_skips_underscore_prefixed_dirs(tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "my-plugin"
    cache = plug / "_cache"
    cache.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (cache / "junk.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"
    )
    import_plugins(_make_cfg(plug.parent))
    assert not marker.exists(), "_cache/ must not be walked"


def test_existing_top_level_plugins_still_work(tmp_path: Path) -> None:
    plug = tmp_path / "plugins"
    plug.mkdir()
    marker = tmp_path / "marker.txt"
    (plug / "single_file.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('ok')"
    )
    import_plugins(_make_cfg(plug))
    assert marker.read_text() == "ok"
