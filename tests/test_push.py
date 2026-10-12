"""Push (#342): the subscriptions and their ops through the real app, and the
sender against a fake push service that decrypts what it is sent."""

import asyncio
import json
import os
import re
import stat
import threading
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from aegis import push
from aegis.ops import Caller, OpError, Principal
from aegis.webpush import b64d, b64e, raw_public

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_webpush import decrypt

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


# -- the sender --------------------------------------------------------------------
class FakePushService(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """A push service for the sender's ``transport``: it records each request,
    decrypts its body as the browser would, and answers ``status``. ``hang``
    makes it never answer, on the async path and the sync one alike."""

    def __init__(self):
        self.ua = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.status = 201
        self.statuses: list[int] = []  # answered first, before ``status``
        self.hang = 0.0
        self.got: list[tuple[httpx.Request, dict]] = []

    def subscription(self, endpoint="https://push.example.test/abc"):
        return {
            "endpoint": endpoint,
            "keys": {"p256dh": b64e(raw_public(self.ua)), "auth": b64e(self.auth)},
        }

    def answer(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(decrypt(request.content, self.ua, self.auth))
        self.got.append((request, payload))
        status = self.statuses.pop(0) if self.statuses else self.status
        return httpx.Response(status)

    def handle_request(self, request):
        time.sleep(self.hang)
        return self.answer(request)

    async def handle_async_request(self, request):
        await asyncio.sleep(self.hang)
        return self.answer(request)

    def payloads(self) -> list[dict]:
        return [p for _, p in self.got]


@pytest.fixture
def service(world, monkeypatch):
    s = FakePushService()
    monkeypatch.setattr(push, "BACKOFF_S", (0, 0))
    monkeypatch.setattr(push.Sender, "transport", s)
    return s


async def subscribe(world, service, sources, who=None, endpoint=None):
    sub = service.subscription(endpoint) if endpoint else service.subscription()
    r = await call(
        world,
        "push.subscribe",
        {"subscription": sub, "device": "x", "sources": sources},
        who,
    )
    return r["id"]


async def test_a_session_that_needs_you_pushes_to_a_device_that_asked(world, service):
    await subscribe(world, service, ["needs_you"])
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention="needs_you", line="Rebase or merge?"))
    await until(lambda: service.got, what="the push")
    [(req, payload)] = service.got
    title = a.wire()["title"] or a.handle
    assert payload == {
        "title": f"{title}: needs you",
        "body": "Rebase or merge?",
        "key": a.log_id,
        "kind": "needs_you",
        "tag": f"{a.log_id}:needs_you",
    }
    h = req.headers
    assert (h["ttl"], h["urgency"], h["content-encoding"]) == (
        "86400",
        "high",
        "aes128gcm",
    )
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", h["topic"])
    assert str(req.url) == "https://push.example.test/abc"
    m = re.fullmatch(r"vapid t=([^,]+), k=(\S+)", h["authorization"])
    key = world.app.push.key
    assert b64d(m.group(2)) == raw_public(key)
    head, body, sig = m.group(1).split(".")
    assert json.loads(b64d(body))["aud"] == "https://push.example.test"
    assert json.loads(b64d(body))["sub"] == "mailto:aegis@localhost"
    raw = b64d(sig)
    der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    key.public_key().verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    await until(
        lambda: world.app.push.subscriptions.list()[0]["last_ok_at"], what="last_ok_at"
    )


async def test_sources_filter_what_each_device_hears(world, service):
    await subscribe(
        world, service, ["error", "toast"], endpoint="https://push.example.test/quiet"
    )
    await subscribe(world, service, ["done"], endpoint="https://push.example.test/done")
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention="needs_you", line="Anyone?"))
    await turn(a, mcp("turn_end", attention="done", line="Finished."))
    await until(lambda: service.got, what="the push")
    await asyncio.sleep(0.3)  # nothing else follows
    [(req, payload)] = service.got
    assert str(req.url) == "https://push.example.test/done"
    # One push: the turn that ended done. The spawn's idle and the reply to
    # the question passed through done too, and are no finished work.
    assert payload["kind"] == "done" and payload["tag"] == f"{a.log_id}:done"
    assert req.headers["urgency"] == "normal"


async def test_a_server_that_boots_with_sessions_waiting_sends_nothing(world, service):
    await subscribe(world, service, ["needs_you"])
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention="needs_you", line="Still there?"))
    await until(lambda: service.got, what="the first push")
    await world.restart()
    a = world.session(a.log_id)
    assert a.wire()["attention"] == "needs_you"
    await asyncio.sleep(0.5)
    assert len(service.got) == 1
    # The sender is listening, though: the next change does push.
    await turn(a, "hello")
    await turn(a, mcp("turn_end", attention="needs_you", line="Now?"))
    await until(lambda: len(service.got) == 2, what="the push after the restart")
    assert service.payloads()[1]["body"] == "Now?"


async def test_an_agents_toast_pushes_and_aegis_own_does_not(world, service):
    await subscribe(world, service, ["toast"])
    a = await world.spawn()
    world.app.toast("Copied the path")
    await turn(a, mcp("toast", text="Spec is ready"))
    await turn(a, mcp("toast", text="Build broke", kind="error"))
    await until(lambda: len(service.got) == 2, what="two toasts")
    first, second = service.payloads()
    assert first == {
        "title": a.handle,
        "body": "Spec is ready",
        "key": a.log_id,
        "kind": "toast",
        "tag": f"toast:{a.log_id}:Spec is ready",
    }
    assert second["title"] == f"{a.handle}: error" and second["body"] == "Build broke"


async def test_a_gone_subscription_is_deleted(world, service):
    await subscribe(world, service, ["toast"])
    service.status = 410
    a = await world.spawn()
    await turn(a, mcp("toast", text="Anyone?"))
    await until(lambda: world.app.push.subscriptions.list() == [], what="the deletion")
    assert len(service.got) == 1


async def test_a_503_is_tried_three_times_then_counted(world, service):
    await subscribe(world, service, ["toast"])
    service.status = 503
    a = await world.spawn()
    await turn(a, mcp("toast", text="Anyone?"))
    await until(
        lambda: world.app.push.subscriptions.list()[0]["failures"] == 1,
        what="the failure",
    )
    assert len(service.got) == 3
    service.statuses, service.status = [503], 201
    await turn(a, mcp("toast", text="Again?"))
    await until(
        lambda: world.app.push.subscriptions.list()[0]["last_ok_at"], what="the success"
    )
    assert (
        len(service.got) == 5
        and world.app.push.subscriptions.list()[0]["failures"] == 1
    )


async def test_a_hanging_push_service_never_delays_the_sessions_channel(
    world, service, monkeypatch
):
    monkeypatch.setattr(push, "TIMEOUT_S", 0.3)
    service.hang = 5
    await subscribe(world, service, ["needs_you"])
    a = await world.spawn()
    seen: list[float] = []

    def listen(frame):
        if frame["t"] == "patch" and any(
            op.get("upsert", {}).get("attention") == "needs_you" for op in frame["ops"]
        ):
            seen.append(time.monotonic())

    world.app.channels.subscribe("sessions", listen)
    t0 = time.monotonic()
    await turn(a, mcp("turn_end", attention="needs_you", line="Quick?"))
    await until(lambda: seen, what="the browser's patch")
    assert seen[0] - t0 < 3
    # Three tries of 0.3 s each, then counted.
    await until(
        lambda: world.app.push.subscriptions.list()[0]["failures"] == 1,
        timeout=5,
        what="the timeouts",
    )
    assert len(service.got) == 0


async def test_push_test_reaches_one_device(world, service):
    one = await subscribe(
        world, service, ["done"], endpoint="https://push.example.test/one"
    )
    await subscribe(world, service, ["done"], endpoint="https://push.example.test/two")
    assert await call(world, "push.test", {"id": one}) == {"status": 201}
    [(req, payload)] = service.got
    assert str(req.url) == "https://push.example.test/one"
    assert payload == {
        "title": "aegis",
        "body": "Push reaches this device.",
        "key": "",
        "kind": "test",
        "tag": "test",
    }
    with pytest.raises(OpError) as e:
        await call(world, "push.test", {"id": one}, CARA)
    assert e.value.code == "not_owner"


async def test_a_long_line_is_cut_to_fit_three_kilobytes(world, service):
    await subscribe(world, service, ["toast"])
    payload = {
        "title": "t",
        "body": "é" * 5000,
        "key": "k",
        "kind": "toast",
        "tag": "x",
    }
    fitted = push.fit(payload)
    assert len(push.encode(fitted)) <= push.MAX_PAYLOAD
    assert fitted["body"].endswith("…") and fitted["title"] == "t"


def fit_ends(payload: dict) -> dict:
    """``push.fit(payload)`` in a daemon thread, failing rather than hanging
    the run if it never returns (it runs on the event loop in the server)."""
    out: list = []
    t = threading.Thread(target=lambda: out.append(push.fit(payload)), daemon=True)
    t.start()
    t.join(5)
    assert out, "fit() did not return"
    return out[0]


async def test_a_long_title_or_toast_is_cut_and_still_pushes(world, service):
    fitted = fit_ends(
        {"title": "ñ" * 4000, "body": "b", "key": "k", "kind": "done", "tag": "x"}
    )
    assert len(push.encode(fitted)) <= push.MAX_PAYLOAD
    assert fitted["title"].endswith("…") and fitted["title"].startswith("ñ")
    text = "😀" * 4000
    tag = f"toast:abc:{text}"
    fitted = fit_ends(
        {"title": "a", "body": text, "key": "abc", "kind": "toast", "tag": tag}
    )
    assert len(push.encode(fitted)) <= push.MAX_PAYLOAD
    assert fitted["tag"].startswith("toast:abc:😀") and fitted["tag"].endswith("…")
    assert fitted["body"].startswith("😀")
    # Through the sender, the event loop stays free and the push goes out.
    # (The toast tool takes 200 characters; App.toast takes any.)
    await subscribe(world, service, ["toast"])
    a = await world.spawn()
    world.app.toast("x" * 4000, source=a)
    await until(lambda: service.got, what="the push")
    [payload] = service.payloads()
    assert payload["body"].startswith("x") and payload["tag"].endswith("…")
