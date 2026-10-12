"""/tutorial in the browser: the menu, the new tab's hint and the Help tab's
button, against a real `aegis serve` with the fake claude. Kept out of
test_browser.py, whose fixtures it borrows."""

import json

import pytest

from aegis.helppages import load

from .test_browser import browser, page, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

TITLE = {p.id: p.title for p in load()}
ENTRIES_NAME = "p => document.querySelector('#entries').innerText.includes(p)"


def meta(server, log_id) -> dict:  # noqa: F811
    path = server.root / ".aegis" / "state" / "sessions" / f"{log_id}.json"
    return json.loads(path.read_text())


def test_slash_tutorial_completes_the_tutorials_and_alone_starts_getting_started(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    first = spawn(page)
    page.fill("#input", "/tutorial ")
    page.wait_for_function(
        "[...document.querySelectorAll('#cmd-rows .cmd-row .nm')]"
        ".some(n => n.textContent === 'getting-started')"
    )
    page.fill("#input", "/tutorial")
    page.press("#input", "Enter")
    page.wait_for_function(
        "f => location.hash.startsWith('#s=') && location.hash.slice(3) !== f",
        arg=first,
    )
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    log_id = page.evaluate("location.hash.slice(3)")
    assert meta(server, log_id)["title"] == f"Tutorial: {TITLE['getting-started']}"
    assert page.errors == []
