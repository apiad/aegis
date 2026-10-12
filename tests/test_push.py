"""Push (#342): the subscriptions and their ops through the real app, and the
sender against a fake push service that decrypts what it is sent."""

import os
import stat

import pytest

from aegis.ops import Caller, OpError, Principal
from aegis.webpush import b64d

from .test_agents import CONFIG, World

BEN = Principal("ben", "member")
CARA = Principal("cara", "member")
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    w.app.principals.add("cara", "member")
    yield w
    await w.stop()


def as_(principal):
    return Caller("user", principal=principal)


def sub(endpoint="https://push.example.test/abc", p256dh=UA_PUBLIC, auth=AUTH):
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


async def call(world, op, params=None, who=None):
    return await world.app.registry.call(
        op, params or {}, as_(who) if who else Caller("user")
    )


async def test_the_key_is_the_same_65_bytes_twice_and_kept_private(world):
    a = await call(world, "push.key", who=BEN)
    b = await call(world, "push.key")
    assert a == b and len(b64d(a["key"])) == 65 and b64d(a["key"])[0] == 4
    assert mode(world.app.roots.state_root / "push" / "vapid.pem") == 0o600


async def test_a_subscription_is_its_callers_with_the_default_sources(world):
    r = await call(
        world,
        "push.subscribe",
        {"subscription": sub(), "device": "Chrome on Android"},
        BEN,
    )
    assert r["id"].startswith("p_") and len(r["id"]) == 10
    [row] = world.app.push.subscriptions.list()
    assert row["owner"] == "ben" and row["device"] == "Chrome on Android"
    assert row["sources"] == ["needs_you", "error", "toast"]
    assert (
        row["endpoint"] == "https://push.example.test/abc"
        and row["keys"]["auth"] == AUTH
    )
    assert row["failures"] == 0 and row["last_ok_at"] is None and row["created_at"] > 0
    path = world.app.roots.state_root / "push" / "subscriptions.json"
    assert mode(path) == 0o600


@pytest.mark.parametrize(
    "params",
    [
        {"subscription": sub(), "device": "x", "sources": ["needs_you", "bogus"]},
        {"subscription": sub(endpoint="http://push.example.test/abc"), "device": "x"},
        {"subscription": sub(p256dh="AAAA"), "device": "x"},
        {"subscription": sub(auth="AAAA"), "device": "x"},
    ],
)
async def test_a_bad_subscription_is_refused(world, params):
    with pytest.raises(OpError) as e:
        await call(world, "push.subscribe", params, BEN)
    assert e.value.code == "bad_params"
    assert world.app.push.subscriptions.list() == []


async def test_an_id_given_replaces_that_subscription(world):
    first = await call(
        world,
        "push.subscribe",
        {"subscription": sub(), "device": "x", "sources": ["done"]},
        BEN,
    )
    again = await call(
        world,
        "push.subscribe",
        {
            "subscription": sub(endpoint="https://push.example.test/new"),
            "device": "x",
            "sources": ["done"],
            "id": first["id"],
        },
        BEN,
    )
    assert again == first
    [row] = world.app.push.subscriptions.list()
    assert row["endpoint"] == "https://push.example.test/new" and row["sources"] == [
        "done"
    ]
    # Another member cannot take it over.
    with pytest.raises(OpError) as e:
        await call(
            world,
            "push.subscribe",
            {"subscription": sub(), "device": "y", "id": first["id"]},
            CARA,
        )
    assert e.value.code == "not_owner"


async def test_only_the_owner_or_an_admin_updates_or_removes(world):
    r = await call(world, "push.subscribe", {"subscription": sub(), "device": "x"}, BEN)
    with pytest.raises(OpError) as e:
        await call(world, "push.update", {"id": r["id"], "sources": ["done"]}, CARA)
    assert e.value.code == "not_owner"
    with pytest.raises(OpError) as e:
        await call(world, "push.remove", {"id": r["id"]}, CARA)
    assert e.value.code == "not_owner"
    await call(
        world,
        "push.update",
        {"id": r["id"], "sources": ["done", "review"], "device": "Pixel"},
        BEN,
    )
    await call(world, "push.update", {"id": r["id"], "sources": ["error"]})  # the admin
    [row] = world.app.push.subscriptions.list()
    assert row["sources"] == ["error"] and row["device"] == "Pixel"
    with pytest.raises(OpError) as e:
        await call(world, "push.update", {"id": r["id"], "sources": ["nope"]}, BEN)
    assert e.value.code == "bad_params"
    await call(world, "push.remove", {"id": r["id"]}, BEN)
    assert world.app.push.subscriptions.list() == []
    with pytest.raises(OpError) as e:
        await call(world, "push.remove", {"id": r["id"]}, BEN)
    assert e.value.code == "unknown_subscription"


async def test_the_list_hides_keys_and_shows_a_member_only_their_own(world):
    await call(world, "push.subscribe", {"subscription": sub(), "device": "ben's"}, BEN)
    await call(
        world, "push.subscribe", {"subscription": sub(), "device": "cara's"}, CARA
    )
    mine = await call(world, "push.list", who=BEN)
    assert [r["device"] for r in mine["subscriptions"]] == ["ben's"]
    assert all("keys" not in r for r in mine["subscriptions"])
    everyone = await call(world, "push.list")
    assert sorted(r["owner"] for r in everyone["subscriptions"]) == ["ben", "cara"]
    assert all("keys" not in r for r in everyone["subscriptions"])


async def test_revoking_a_members_token_deletes_their_subscriptions(world):
    await call(world, "push.subscribe", {"subscription": sub(), "device": "ben's"}, BEN)
    await call(
        world, "push.subscribe", {"subscription": sub(), "device": "cara's"}, CARA
    )
    await call(world, "token.revoke", {"name": "ben"})
    assert [r["owner"] for r in world.app.push.subscriptions.list()] == ["cara"]


async def test_subscriptions_survive_a_restart(world):
    r = await call(world, "push.subscribe", {"subscription": sub(), "device": "x"}, BEN)
    key = await call(world, "push.key")
    await world.restart()
    assert [s["id"] for s in world.app.push.subscriptions.list()] == [r["id"]]
    assert await call(world, "push.key") == key
