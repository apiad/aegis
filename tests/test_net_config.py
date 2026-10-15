"""The ``network:`` block: defaults, a refused block, and its doctor row."""

from pathlib import Path

import pytest

from aegis.config import Snapshot
from aegis.doctor import _network
from aegis.net.config import NetConfig, network_from


def test_no_block_is_every_default_with_a_speed_test_each_quarter_hour():
    cfg, error = network_from(None)
    assert error is None
    assert cfg == NetConfig()
    assert cfg.speed_interval == 900.0 and cfg.enabled


def test_a_block_overrides_only_what_it_names():
    cfg, error = network_from(
        {"speed_interval": 0, "anchors": ["[2606:4700::1111]:443"], "enabled": False}
    )
    assert error is None
    assert cfg.speed_interval == 0.0
    assert cfg.anchors == (("2606:4700::1111", 443),)
    assert not cfg.enabled and cfg.interval == 20.0


@pytest.mark.parametrize(
    "block, said",
    [
        ("yes", "must be a mapping"),
        ({"intervall": 5}, "no key 'intervall'"),
        ({"interval": 0}, "network.interval 0"),
        ({"timeout": -1}, "network.timeout -1"),
        ({"speed_timeout": True}, "network.speed_timeout True"),
        ({"speed_interval": -5}, "0 (off)"),
        ({"speed_bytes": 0}, "byte count"),
        ({"enabled": "no"}, "true or false"),
        ({"anchors": []}, "non-empty list"),
        ({"anchors": ["2606:4700::1111"]}, "bracketed"),
    ],
)
def test_a_wrong_block_keeps_every_default_and_says_why(block, said):
    cfg, error = network_from(block)
    assert cfg == NetConfig()
    assert said in error


def test_doctor_reports_the_block():
    ok = _network(Snapshot.parse(Path("x"), None, {}))
    assert ok.level == "ok" and "1 MB speed test every 15 min" in ok.message
    off = _network(Snapshot.parse(Path("x"), None, {"network": {"speed_interval": 0}}))
    assert "no speed test" in off.message
    bad = _network(Snapshot.parse(Path("x"), None, {"network": {"interval": 0}}))
    assert bad.level == "error" and bad.where == "network"
