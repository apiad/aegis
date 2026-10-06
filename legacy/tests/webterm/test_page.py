"""Everything the page names exists, and is served.

Resolved from the files rather than from a list here, so adding an import
to term.js cannot outrun this test.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

from aegis.webterm.app import build_webterm_app
from aegis.webterm.auth import COOKIE

STATIC = Path(__file__).resolve().parents[2] / "src" / "aegis" / "webterm" / "static"


def _referenced() -> set[str]:
    html = (STATIC / "index.html").read_text()
    refs = set(re.findall(r'(?:src|href)="(/static/[^"]+)"', html))
    for js in STATIC.glob("*.js"):
        refs |= set(re.findall(r'from "(/static/[^"]+)"', js.read_text()))
    return refs


def test_every_static_path_the_page_names_exists():
    refs = _referenced()
    assert "/static/term.js" in refs and "/static/vendor/xterm/xterm.mjs" in refs
    for ref in refs:
        assert (STATIC / ref.removeprefix("/static/")).is_file(), ref


def test_the_vendored_versions_and_licenses_are_recorded():
    vendor = STATIC / "vendor" / "xterm"
    assert (vendor / "VERSIONS").read_text().split("\n")[:2] == [
        "@xterm/xterm 6.0.0",
        "@xterm/addon-fit 0.11.0",
    ]
    for name in ("LICENSE-xterm", "LICENSE-addon-fit"):
        assert "MIT" in (vendor / name).read_text()


def test_the_page_and_its_modules_are_served():
    async def connect():
        raise AssertionError("not reached")

    client = TestClient(
        build_webterm_app(token="t", connect=connect), base_url="https://testserver"
    )
    client.cookies.set(COOKIE, "t")
    assert 'id="term"' in client.get("/").text
    for ref in _referenced():
        r = client.get(ref)
        assert r.status_code == 200, ref
        if ref.endswith((".js", ".mjs")):
            assert "javascript" in r.headers["content-type"], (
                f"{ref} served as {r.headers['content-type']}; a browser "
                "refuses to run a module with a non-JS MIME type"
            )


def test_one_view_per_tab():
    js = (STATIC / "term.js").read_text()
    assert "sessionStorage" in js and "localStorage" not in js, (
        "two tabs of one browser must not share a view: a view has one geometry"
    )
    assert "BroadcastChannel" in js, (
        "Duplicate tab copies sessionStorage; without a claim check the copy "
        "is refused by the daemon and retries forever"
    )


def test_the_page_acts_on_a_taken_view_instead_of_reconnecting_for_ever():
    """`relay` gives up on a view another tab holds and sends `view_taken`;
    a page that ignored it would sit in "reconnecting" until closed."""
    from aegis.webterm.relay import VIEW_TAKEN

    js = (STATIC / "term.js").read_text()
    assert "view_taken" in js, "the page ignores the frame relay.py sends"
    assert 'sessionStorage.removeItem("aegis-view")' in js, (
        "the page kept the id the daemon refuses, so the reload loops"
    )
    assert '"view_taken"' in VIEW_TAKEN
