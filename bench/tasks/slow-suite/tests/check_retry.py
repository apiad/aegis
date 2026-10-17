"""Hidden checks for slow-suite: the retry policy, against scripted failures, fast."""

import time

import pytest
from hidden_server import FakeServer

from httpc import Client, ConnectionFailed, HTTPError


@pytest.fixture(scope="module")
def server():
    with FakeServer() as s:
        yield s


def timed(fn):
    start = time.monotonic()
    try:
        return fn(), time.monotonic() - start
    except Exception as e:
        return e, time.monotonic() - start


@pytest.mark.parametrize("status", [502, 503, 504])
def test_each_gateway_status_is_retried_until_success(server, status):
    server.script(f"/s{status}", [status, status, 200])
    r, took = timed(lambda: Client(server.url).get(f"/s{status}"))
    assert r.status == 200 and server.hits[f"/s{status}"] == 3
    assert 0.6 <= took < 1.0


@pytest.mark.parametrize("status", [400, 401, 404, 409, 429, 500, 501])
def test_other_statuses_fail_on_the_first_attempt(server, status):
    server.script(f"/n{status}", [status, 200])
    e, took = timed(lambda: Client(server.url).get(f"/n{status}"))
    assert isinstance(e, HTTPError) and e.status == status
    assert server.hits[f"/n{status}"] == 1 and took < 0.2


def test_three_failures_raise_the_last_one_without_a_fourth_wait(server):
    server.script("/gone", [502, 503, 504])
    e, took = timed(lambda: Client(server.url).get("/gone"))
    assert isinstance(e, HTTPError) and e.status == 504
    assert server.hits["/gone"] == 3 and 0.6 <= took < 1.0


def test_a_reset_is_retried_with_the_same_backoff(server):
    server.script("/r", ["reset", "reset", 200])
    r, took = timed(lambda: Client(server.url).get("/r"))
    assert r.ok and server.hits["/r"] == 3 and 0.6 <= took < 1.0


def test_three_resets_raise_connection_failed(server):
    server.script("/rr", ["reset"])
    e, _ = timed(lambda: Client(server.url).get("/rr"))
    assert isinstance(e, ConnectionFailed) and server.hits["/rr"] == 3


def test_a_retried_post_resends_its_body(server):
    server.script("/p", [503, 200])
    r = Client(server.url, token="t").post("/p", {"sku": "B-2", "qty": 5})
    assert r.json()["body"] == {"sku": "B-2", "qty": 5} and r.json()["auth"] == "Bearer t"


def test_a_refused_connection_fails_at_once():
    e, took = timed(lambda: Client("http://127.0.0.1:9").get("/"))
    assert isinstance(e, ConnectionFailed) and took < 0.2


def test_success_needs_one_request(server):
    r, took = timed(lambda: Client(server.url).get("/fine"))
    assert r.ok and server.hits["/fine"] == 1 and took < 0.2
