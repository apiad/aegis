"""Parking: the pure rules, the config block and the leftover-process check.
The sessions themselves are in test_park_e2e.py."""

import pytest

from aegis.config import park_after, park_from
from aegis.doctor import _park


def test_the_block_maps_each_harness_to_its_seconds():
    p = park_from({"claude-code": 1200, "codex": 1800})
    assert p == {"claude-code": 1200, "codex": 1800}
    assert park_after(p, "claude-code") == 1200
    assert park_after(p, "opencode") is None


def test_no_block_parks_nothing():
    assert park_from(None) == {}
    assert park_after(park_from(None), "claude-code") is None


@pytest.mark.parametrize(
    "raw, says",
    [
        ([1200], "mapping"),
        ({"cluade-code": 1200}, "no harness 'cluade-code'"),
        ({"claude-code": 0}, "positive"),
        ({"claude-code": True}, "positive"),
        ({"claude-code": "20m"}, "positive"),
    ],
)
def test_a_wrong_block_parks_nothing_and_says_why(raw, says):
    p = park_from(raw)
    assert says in p["error"]
    assert park_after(p, "claude-code") is None


def test_doctor_names_the_mistake_and_warns_on_an_unmeasured_harness():
    assert _park(park_from({"claude-code": 0})).level == "error"
    assert _park({}).level == "ok"
    assert _park({"claude-code": 1200}).level == "ok"
    f = _park({"claude-code": 1200, "codex": 600})
    assert f.level == "warn" and "codex" in f.message
