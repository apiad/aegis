import pytest

from aegis.config import FleetConfig
from aegis.config.yaml_loader import ConfigError, load_config


def test_no_fleet_block_means_the_defaults(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    assert load_config(tmp_path).fleet == FleetConfig()


def test_a_fleet_block_overrides_what_it_names(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "fleet:\n  recap: off\n  recap_interval_s: 300\n")
    cfg = load_config(tmp_path).fleet
    assert cfg.recap == "off"
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


@pytest.mark.parametrize("value", ["0", "-5", "0.5", "true", "10"])
def test_a_recap_interval_that_would_pay_per_tick_is_refused(tmp_path, value):
    """recap_interval_s gates a paid one-shot call. 0 or less makes the gate
    `since_last_s >= interval` always true — a recap on every check for every
    watched working session. 0.5 used to truncate to 0 and `true` to 1."""
    (tmp_path / ".aegis.yaml").write_text(f"fleet:\n  recap_interval_s: {value}\n")
    with pytest.raises(ConfigError, match="fleet.recap_interval_s"):
        load_config(tmp_path)


@pytest.mark.parametrize("key,value", [
    ("recap_interval_s", "abc"),
    ("recap_interval_s", ""),
    ("recap_after_s", ""),
    ("recap_after_s", "-1"),
    ("recap_after_s", "1.5"),
])
def test_a_bad_fleet_number_is_a_config_error_naming_the_key(tmp_path, key, value):
    """Every boot path catches only ConfigError; a bare ValueError or TypeError
    reaches the operator as a traceback that never names the key."""
    (tmp_path / ".aegis.yaml").write_text(f"fleet:\n  {key}: {value}\n")
    with pytest.raises(ConfigError, match=f"fleet.{key}"):
        load_config(tmp_path)


@pytest.mark.parametrize("value", ["on", '"on"'])
def test_on_is_refused_naming_fleet_recap(tmp_path, value):
    """`on` was removed: it paid for sessions nobody had on screen, which
    the watcher gate exists to prevent. Quoted or not, it must not load as
    anything, least of all as `watched`."""
    (tmp_path / ".aegis.yaml").write_text(f"fleet:\n  recap: {value}\n")
    with pytest.raises(ConfigError, match="fleet.recap"):
        load_config(tmp_path)


def test_off_unquoted_is_the_string_off(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("fleet:\n  recap: off\n")
    assert load_config(tmp_path).fleet.recap == "off"


def test_the_smallest_sane_interval_loads(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "fleet:\n  recap_after_s: 0\n  recap_interval_s: 30\n")
    cfg = load_config(tmp_path).fleet
    assert (cfg.recap_after_s, cfg.recap_interval_s) == (0, 30)
