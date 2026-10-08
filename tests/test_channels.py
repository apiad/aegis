import asyncio

import pytest

from aegis.channels import Channels
from aegis.ops import OpError


def make():
    state = {"n": 0}
    return Channels(
        lambda name, since=None: (
            (lambda: {**state, "since": since}) if name == "c" else None
        )
    ), state


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


# -- Throttle: one channel's ops, merged by key, at most once per interval ----


def throttled(every=0.05):
    from aegis.channels import Throttle

    sent: list[list[dict]] = []
    t = Throttle(
        sent.append,
        lambda op: (
            (op["upsert"]["id"], op.get("now", False))
            if "upsert" in op
            else (op["remove"], True)
        ),
        every,
    )
    return t, sent


async def test_the_first_ops_after_a_quiet_interval_go_out_at_once():
    t, sent = throttled()
    t.add([{"upsert": {"id": "a", "n": 1}}])
    assert sent == [[{"upsert": {"id": "a", "n": 1}}]]


async def test_ops_inside_the_interval_go_out_together_last_per_key_winning():
    t, sent = throttled()
    t.add([{"upsert": {"id": "a", "n": 1}}, {"upsert": {"id": "b", "n": 1}}])
    t.add([{"upsert": {"id": "a", "n": 2}}])
    t.add([{"upsert": {"id": "b", "n": 2}}])
    t.add([{"upsert": {"id": "a", "n": 3}}])
    assert len(sent) == 1
    await asyncio.sleep(0.08)
    assert sent[1:] == [
        [{"upsert": {"id": "b", "n": 2}}, {"upsert": {"id": "a", "n": 3}}]
    ]
    await asyncio.sleep(0.08)
    assert len(sent) == 2, "nothing pending, nothing sent"


async def test_an_urgent_op_goes_out_at_once_with_what_is_pending():
    t, sent = throttled()
    t.add([{"upsert": {"id": "a", "n": 1}}])
    t.add([{"upsert": {"id": "a", "n": 2}}])
    t.add([{"upsert": {"id": "b", "n": 1}, "now": True}])
    assert sent[1:] == [
        [{"upsert": {"id": "a", "n": 2}}, {"upsert": {"id": "b", "n": 1}, "now": True}]
    ]
    t.add([{"upsert": {"id": "a", "n": 3}}])
    t.add([{"remove": "a"}])
    assert sent[2:] == [[{"remove": "a"}]], "the removal supersedes the pending upsert"
    await asyncio.sleep(0.08)
    assert len(sent) == 3


async def test_the_rate_is_bounded_however_many_keys_change():
    t, sent = throttled(every=0.05)
    end = asyncio.get_running_loop().time() + 0.5
    n = 0
    while asyncio.get_running_loop().time() < end:
        t.add([{"upsert": {"id": f"s{n % 100}", "n": n}}])
        n += 1
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.08)
    assert n > 100
    assert len(sent) <= 0.5 / 0.05 + 2
    last = {op["upsert"]["id"]: op["upsert"]["n"] for ops in sent for op in ops}
    assert len(last) == 100 and max(last.values()) == n - 1


def test_since_reaches_the_channel_resolver():
    ch, _ = make()
    got = []
    ch.subscribe("c", got.append, since=7)
    ch.subscribe("c", got.append)
    assert [m["data"]["since"] for m in got] == [7, None]
