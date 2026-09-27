"""The aegis-aware web layer is gone, and the daemon is fine without it.

It reached into the brain by attribute — eleven manager methods — and grew a
message per feature until it fell behind the TUI. `aegis web` replaced it
with a relay that knows no aegis concept.
"""

import importlib.util
from pathlib import Path

import aegis


def test_the_web_package_is_gone():
    assert importlib.util.find_spec("aegis.web") is None
    assert not (Path(aegis.__file__).parent / "web").exists()


def test_a_config_with_a_web_block_still_boots_a_brain(tmp_path):
    """Review Focus 3: the daemon stopped reading the block in stage 5b, and
    a config written before that must not raise now the code is deleted."""
    from aegis.cli import load_boot_config
    from aegis.config.roots import AegisRoots

    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        "    model: opus\nweb:\n  bind: 127.0.0.1\n  port: 8899\n  token: secret\n",
        encoding="utf-8",
    )
    boot = load_boot_config(AegisRoots.for_project(tmp_path))
    assert boot.default_agent == "main"
    assert not hasattr(boot, "web")


def test_a_state_dir_from_the_old_version_is_not_rejected(tmp_path):
    """Review Focus 5: an upgraded install has `web.port` and old view files
    lying in .aegis/state. Reading the config must not care."""
    from aegis.cli import load_boot_config
    from aegis.config.roots import AegisRoots
    from aegis.state.workspace import state_dir

    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        "    model: opus\n",
        encoding="utf-8",
    )
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "web.port").write_text("8899")
    (sd / "views").mkdir(exist_ok=True)
    (sd / "views" / "legacy.json").write_text("{}")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).default_agent == "main"
