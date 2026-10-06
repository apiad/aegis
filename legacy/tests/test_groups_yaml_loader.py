from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def _write_base(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
""")


def test_loads_inline_groups_defaults_and_presets(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
groups:
  defaults:
    broadcast_timeout: 300
    default_reducer: join_by_handle
  presets:
    code_audit:
      profiles: [sec, style, logic]
""")
    cfg = load_config(tmp_path)
    assert cfg.groups["defaults"]["broadcast_timeout"] == 300
    assert cfg.groups["defaults"]["default_reducer"] == "join_by_handle"
    assert cfg.groups["presets"]["code_audit"]["profiles"] == \
        ["sec", "style", "logic"]


def test_loads_overlay_group_files(tmp_path: Path):
    _write_base(tmp_path)
    (tmp_path / ".aegis" / "groups").mkdir(parents=True)
    (tmp_path / ".aegis" / "groups" / "code_audit.yaml").write_text(
        "profiles: [sec, style, logic]\n")
    cfg = load_config(tmp_path)
    assert cfg.groups["presets"]["code_audit"]["profiles"] == \
        ["sec", "style", "logic"]


def test_inline_overlay_conflict_is_fail_loud(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("""\
default_agent: claude
agents:
  claude: {provider: claude-code, model: opus, effort: high, permission: auto}
groups:
  presets:
    code_audit:
      profiles: [a, b]
""")
    (tmp_path / ".aegis" / "groups").mkdir(parents=True)
    (tmp_path / ".aegis" / "groups" / "code_audit.yaml").write_text(
        "profiles: [c, d]\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_no_groups_section_yields_empty_dict(tmp_path: Path):
    _write_base(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.groups == {}
