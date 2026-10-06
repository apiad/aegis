import pytest

from aegis2.channels import Channels
from aegis2.ops import OpError


def make():
    state = {"n": 0}
    return Channels(lambda name: (lambda: dict(state)) if name == "c" else None), state


def test_snapshot_then_numbered_patches():
    ch, _ = make()
    got = []
    ch.subscribe("c", got.append)
    ch.publish("c", [{"set": {"n": 1}}])
    ch.publish("c", [{"set": {"n": 2}}])
    assert [(m["t"], m["seq"]) for m in got] == [
        ("snapshot", 0),
        ("patch", 1),
        ("patch", 2),
    ]


def test_each_subscription_numbers_its_own_patches():
    ch, _ = make()
    a, b = [], []
    ch.subscribe("c", a.append)
    ch.publish("c", [{"x": 1}])
    ch.subscribe("c", b.append)
    ch.publish("c", [{"x": 2}])
    assert [m["seq"] for m in a] == [0, 1, 2]
    assert [m["seq"] for m in b] == [0, 1]


def test_unsubscribe_stops_patches_and_empty_ops_send_nothing():
    ch, _ = make()
    got = []
    sub = ch.subscribe("c", got.append)
    ch.publish("c", [])
    ch.unsubscribe(sub)
    ch.publish("c", [{"x": 1}])
    assert len(got) == 1


def test_unknown_channel_is_refused():
    ch, _ = make()
    with pytest.raises(OpError) as e:
        ch.subscribe("nope", print)
    assert e.value.code == "unknown_channel"
