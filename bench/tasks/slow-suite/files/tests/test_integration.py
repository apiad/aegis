import time

import pytest

from httpc import Client, ConnectionFailed, HTTPError
from httpc.pagination import all_items


def test_get_echoes_the_path(server):
    assert Client(server.url).get("/orders/7").json()["path"] == "/orders/7"


def test_post_sends_json(server):
    assert Client(server.url).post("/orders", {"sku": "A-1", "qty": 2}).json()["body"] == {"sku": "A-1", "qty": 2}


def test_token_goes_in_the_header(server):
    assert Client(server.url, token="t0k").get("/me").json()["auth"] == "Bearer t0k"


def test_pagination_collects_every_page(server):
    assert len(all_items(Client(server.url), "/items")) == 9


def test_404_raises_at_once(server):
    server.script("/missing", [404])
    with pytest.raises(HTTPError) as e:
        Client(server.url).get("/missing")
    assert e.value.status == 404 and server.hits["/missing"] == 1


def test_400_is_not_retried(server):
    server.script("/bad", [400, 200])
    with pytest.raises(HTTPError):
        Client(server.url).post("/bad", {})
    assert server.hits["/bad"] == 1


def test_500_is_not_retried(server):
    server.script("/boom", [500, 200])
    with pytest.raises(HTTPError):
        Client(server.url).get("/boom")
    assert server.hits["/boom"] == 1


def test_503_then_success(server):
    server.script("/orders", [503, 503, 200])
    assert Client(server.url).get("/orders").status == 200
    assert server.hits["/orders"] == 3


def test_502_and_504_are_retried(server):
    server.script("/a", [502, 200])
    server.script("/b", [504, 200])
    c = Client(server.url)
    assert c.get("/a").ok and c.get("/b").ok


def test_gives_up_after_three_attempts(server):
    server.script("/down", [503])
    with pytest.raises(HTTPError) as e:
        Client(server.url).get("/down")
    assert e.value.status == 503 and server.hits["/down"] == 3


def test_backoff_doubles_from_0_2_seconds(server):
    server.script("/slow", [502, 502, 200])
    start = time.monotonic()
    Client(server.url).get("/slow")
    assert 0.6 <= time.monotonic() - start < 1.2


def test_a_reset_connection_is_retried(server):
    server.script("/flaky", ["reset", 200])
    assert Client(server.url).get("/flaky").ok


def test_a_refused_connection_is_not_retried():
    start = time.monotonic()
    with pytest.raises(ConnectionFailed):
        Client("http://127.0.0.1:9").get("/")
    assert time.monotonic() - start < 0.2
