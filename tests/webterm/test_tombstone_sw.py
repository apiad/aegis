"""Review Focus 1: the old PWA's service worker outlives its server.

It was registered at scope `/` on dev.apiad.net and intercepts every GET for
that origin. Deleting the old client does not unregister it — the browser
only drops a worker whose script it can no longer fetch, and only on an
update check. Serving a worker that unregisters itself is how you kill one.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from aegis.webterm.app import build_webterm_app


def _client():
    async def connect():
        raise AssertionError("not reached")

    return TestClient(
        build_webterm_app(token="t", connect=connect), base_url="https://testserver"
    )


def test_the_worker_is_served_without_a_cookie():
    """An old worker's update check carries no cookie of ours. A 401 here
    leaves it installed and intercepting for ever."""
    r = _client().get("/service-worker.js")
    assert r.status_code == 200, r.status_code
    assert "javascript" in r.headers["content-type"]


def test_the_worker_unregisters_itself_and_drops_its_caches():
    body = _client().get("/service-worker.js").text
    assert "self.registration.unregister()" in body
    assert "caches.keys()" in body and "caches.delete" in body
    assert "skipWaiting" in body
    assert 'addEventListener("fetch"' not in body, (
        "a tombstone must not intercept fetches; that is what it is removing"
    )


def test_it_is_not_cached_by_the_browser():
    r = _client().get("/service-worker.js")
    assert "no-cache" in r.headers.get("cache-control", "").lower(), r.headers
