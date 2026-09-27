"""The only door that faces a network.

`base_url` is https because the session cookie is Secure, and an http test
client would never send it back: every cookie test would pass by testing
nothing.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.daemon.protocol import encode_data, hello
from aegis.webterm.app import build_webterm_app
from aegis.webterm.auth import COOKIE, token_ok

TOKEN = "s3cret-token"


class _Refused(Exception):
    pass


@pytest.fixture
def world(tmp_path):
    (tmp_path / "index.html").write_text('<div id="term"></div>')
    calls = []

    async def connect():
        calls.append(1)
        raise _Refused("no daemon in this test")

    app = build_webterm_app(token=TOKEN, connect=connect, static_dir=tmp_path)
    return TestClient(app, base_url="https://testserver"), calls


def _logged_in(client):
    client.cookies.set(COOKIE, TOKEN)
    return client


def test_token_ok_is_exact():
    assert token_ok(TOKEN, TOKEN)
    for bad in (None, "", TOKEN + "x", TOKEN[:-1]):
        assert not token_ok(bad, TOKEN)
    assert not token_ok("", "")


def test_healthz_needs_no_token(world):
    client, _ = world
    assert client.get("/healthz").json() == {"ok": True}


def test_the_login_url_trades_the_token_for_a_cookie(world):
    client, _ = world
    r = client.get(f"/?t={TOKEN}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/", "the token must not survive into the next URL"
    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=lax", f"{COOKIE}="):
        assert attr in cookie, f"{attr} missing from {cookie}"


def test_a_wrong_login_token_sets_nothing(world):
    client, _ = world
    r = client.get("/?t=nope", follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_the_page_needs_the_cookie(world):
    client, _ = world
    assert client.get("/").status_code == 401
    r = _logged_in(client).get("/")
    assert r.status_code == 200 and 'id="term"' in r.text


@pytest.mark.parametrize("cookie", [None, "", "nope", TOKEN + "x"])
def test_a_socket_without_the_cookie_is_refused_before_the_daemon(cookie, world):
    client, calls = world
    if cookie is not None:
        client.cookies.set(COOKIE, cookie)
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/term") as ws:
            ws.send_bytes(hello("web-1", 80, 24))
            ws.receive_bytes()
    assert e.value.code == 4401
    assert calls == [], "an unauthenticated socket reached the daemon"


def test_a_socket_from_another_origin_is_refused_before_the_daemon(world):
    """SameSite stops other sites, not sibling subdomains: a page on any
    other host under the same domain is same-site, and its socket carries
    the cookie."""
    client, calls = _logged_in(world[0]), world[1]
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(
            "/term", headers={"origin": "https://other.example"}
        ) as ws:
            ws.send_bytes(hello("web-1", 80, 24))
            ws.receive_bytes()
    assert e.value.code == 4403
    assert calls == [], "a foreign origin reached the daemon"


def test_an_authenticated_socket_reaches_the_daemon_only_after_hello(world):
    client, calls = _logged_in(world[0]), world[1]
    with client.websocket_connect("/term") as ws:
        ws.send_bytes(encode_data(b"no hello first"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert calls == []
    with client.websocket_connect("/term") as ws:
        ws.send_bytes(hello("web-1", 80, 24))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert calls == [1]
