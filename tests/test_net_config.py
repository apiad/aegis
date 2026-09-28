"""The `network:` config block.

Anchors are parsed here rather than at probe time: a malformed one
discovered by the prober is a row that reads "not sampled yet" forever and
never says why.
"""
from __future__ import annotations

import pytest

from aegis.config import NetworkConfig
from aegis.config.yaml_loader import ConfigError, _build_network


def test_an_absent_block_gives_the_defaults():
    assert _build_network(None) == NetworkConfig()


def test_probing_is_on_by_default_and_the_megabyte_is_not():
    """300 bytes every five minutes is invisible. A repeating download from a
    speed-test host is the shape that gets noticed on a restricted network,
    so it ships off."""
    default = NetworkConfig()
    assert default.enabled is True
    assert default.speed_interval == 0.0


def test_the_default_anchors_are_already_parsed_pairs():
    """NetService hands these straight to probe.reach."""
    for entry in NetworkConfig().anchors:
        host, port = entry
        assert isinstance(host, str)
        assert isinstance(port, int)


def test_every_key_can_be_overridden():
    got = _build_network(
        {
            "enabled": False,
            "interval": 5,
            "trace_interval": 60,
            "speed_interval": 900,
            "speed_bytes": 2_000_000,
            "timeout": 1.5,
            "anchors": ["9.9.9.9:853"],
        }
    )
    assert got.enabled is False
    assert got.interval == 5.0
    assert got.trace_interval == 60.0
    assert got.speed_interval == 900.0
    assert got.speed_bytes == 2_000_000
    assert got.timeout == 1.5
    assert got.anchors == (("9.9.9.9", 853),)


def test_an_ipv6_anchor_survives_the_loader():
    """Review Focus 1, at the layer a user actually types it."""
    got = _build_network({"anchors": ["[2606:4700:4700::1111]:443"]})
    assert got.anchors == (("2606:4700:4700::1111", 443),)


def test_a_non_mapping_block_is_refused():
    with pytest.raises(ConfigError):
        _build_network(["1.1.1.1:443"])


def test_an_empty_anchor_list_is_refused_rather_than_silently_disabling():
    """Review Focus 5. With no anchors every probe fails and the row reads
    "not sampled yet" forever, which looks like a bug in aegis."""
    with pytest.raises(ConfigError):
        _build_network({"anchors": []})


def test_a_malformed_anchor_names_itself():
    with pytest.raises(ConfigError) as caught:
        _build_network({"anchors": ["1.1.1.1"]})
    assert "anchors" in str(caught.value)


def test_a_non_positive_speed_byte_count_is_refused():
    """Review Focus 5's other half."""
    with pytest.raises(ConfigError):
        _build_network({"speed_bytes": 0})


def test_the_service_reads_every_attribute_the_config_supplies():
    """The two halves of this feature agree on names.

    Task 3's tests run against a stand-in, so nothing there would notice a
    rename on this side. Bound from the real dataclass rather than a written
    list, so adding a field cannot leave this test passing by omission.
    """
    import inspect

    from aegis.net.service import NetService

    source = inspect.getsource(NetService)
    for name in NetworkConfig().__dataclass_fields__:
        assert f"_cfg.{name}" in source, (
            f"NetworkConfig.{name} is not read by NetService"
        )


def test_the_megabyte_probe_gets_its_own_timeout_not_the_handshakes():
    """A 3s budget is right for a TCP handshake and wrong for a 1 MB body.

    Found by running `/net` on zion: `unmeasured — ConnectTimeout (0 of
    1000000 bytes in 3.16s)`, while a curl of the same URL needed 7.16s. One
    shared `timeout` meant the throughput probe failed on exactly the slow
    links it exists to measure and succeeded only where the answer did not
    matter.
    """
    default = NetworkConfig()
    assert default.speed_timeout > default.timeout
    got = _build_network({"speed_timeout": 45})
    assert got.speed_timeout == 45.0


def test_a_non_positive_speed_timeout_is_refused():
    with pytest.raises(ConfigError):
        _build_network({"speed_timeout": 0})
