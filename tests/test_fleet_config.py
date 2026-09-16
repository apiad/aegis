import pytest

from aegis.config import FleetConfig
from aegis.config.yaml_loader import ConfigError, load_config


def test_no_fleet_block_means_the_defaults(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    assert load_config(tmp_path).fleet == FleetConfig()


def test_a_fleet_block_overrides_what_it_names(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "fleet:\n  recap: on\n  recap_interval_s: 300\n")
    cfg = load_config(tmp_path).fleet
    assert cfg.recap == "on"
    assert cfg.recap_interval_s == 300
    assert cfg.recap_after_s == 60, "an unnamed key keeps its default"


def test_an_unknown_recap_mode_fails_loud(tmp_path):
    """A typo that silently means 'off' is a dashboard with no lines and no
    explanation."""
    (tmp_path / ".aegis.yaml").write_text("fleet:\n  recap: sometimes\n")
    with pytest.raises(ConfigError, match="fleet.recap"):
        load_config(tmp_path)


def test_a_non_mapping_fleet_block_fails_loud(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("fleet: true\n")
    with pytest.raises(ConfigError, match="fleet"):
        load_config(tmp_path)
