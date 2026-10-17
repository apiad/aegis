"""Hidden checks for slow-suite: the retry policy, against scripted failures, fast."""

import time

import pytest
from hidden_server import FakeServer

import httpc
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


# -- the follow-ups: Retry-After, then a configurable policy --


@pytest.mark.parametrize(
    "value, low, high",
    [("1", 1.0, 1.35), ("30", 2.0, 2.35), ("soon", 0.2, 0.45), ("0", 0.0, 0.15)],
)
def test_a_503_retry_after_replaces_the_backoff_up_to_two_seconds(server, value, low, high):
    path = f"/ra{value}"
    server.script(path, [(503, {"Retry-After": value}), 200])
    r, took = timed(lambda: Client(server.url).get(path))
    assert r.status == 200 and server.hits[path] == 2
    assert low <= took < high, took


def test_the_policy_is_configurable():
    retry = httpc.Retry(attempts=2, backoff_s=0.1)
    with FakeServer() as s:
        s.script("/c", [503, 503, 200])
        e, took = timed(lambda: Client(s.url, retry=retry).get("/c"))
        assert isinstance(e, HTTPError) and s.hits["/c"] == 2 and 0.1 <= took < 0.3
        s.script("/off", [503, 200])
        e, took = timed(lambda: Client(s.url, retry=httpc.Retry(attempts=1)).get("/off"))
        assert isinstance(e, HTTPError) and s.hits["/off"] == 1 and took < 0.15
        s.script("/four", [502, 502, 502, 200])
        r, took = timed(lambda: Client(s.url, retry=httpc.Retry(attempts=4, backoff_s=0.05)).get("/four"))
        assert r.status == 200 and s.hits["/four"] == 4 and 0.35 <= took < 0.6


def test_the_default_policy_is_three_attempts_from_point_two_seconds():
    assert httpc.Retry().attempts == 3 and httpc.Retry().backoff_s == pytest.approx(0.2)
