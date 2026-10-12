"""/tutorial in the browser: the menu, the new tab's hint and the Help tab's
button, against a real `aegis serve` with the fake claude. Kept out of
test_browser.py, whose fixtures it borrows."""

import getpass
import json
import time

import pytest

from aegis.helppages import load

from .test_browser import browser, linked, new_page, page, pick, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

TITLE = {p.id: p.title for p in load()}
ENTRIES_NAME = "p => document.querySelector('#entries').innerText.includes(p)"


def eventually(pred, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.05)


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


def test_the_hint_spawns_with_the_chips_and_opens_the_guides_tab(
    server,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_selector("#sp-hints[data-ready]", state="attached")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.is_visible("#hint-tutorial")
    pick(page, "#sp-effort", "max")
    # The second click lands while the first runs, and starts nothing.
    page.dblclick("#hint-tutorial-go")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    m = meta(server, log_id)
    assert (m["agent"], m["effort"]) == ("opus", "max")
    assert m["title"] == f"Tutorial: {TITLE['getting-started']}"
    assert page.locator("#tablist .tab").count() == 1
    page.click("#tab-add")
    page.wait_for_selector("#sp-hints[data-ready]", state="attached")
    assert page.is_hidden("#hint-tutorial")
    assert page.errors == []


def test_the_cross_dismisses_the_hint_on_every_device(
    server,  # noqa: F811
    browser,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_selector("#sp-hints[data-ready]", state="attached")
    assert page.is_visible("#hint-tutorial")
    assert page.locator("#hint-tutorial-x svg.ic").count() == 1
    page.click("#hint-tutorial-x")
    assert page.is_hidden("#hint-tutorial")
    record = server.root / ".aegis" / "state" / "help" / f"{getpass.getuser()}.json"
    eventually(lambda: record.exists() and json.loads(record.read_text())["tutorial"])
    # Another browser: the record is the server's, not this browser's storage.
    errors: list = []
    other = new_page(browser, errors)
    other.goto(server.url + "#new")
    other.wait_for_selector("#sp-hints[data-ready]", state="attached")
    assert other.is_hidden("#hint-tutorial")
    assert errors == [] and page.errors == []


def test_the_hint_hides_while_the_composer_points_at_a_linked_server(
    linked,  # noqa: F811
    page,  # noqa: F811
):
    page.goto(linked.alpha.url)
    page.click("#tab-add")
    page.wait_for_selector("#sp-hints[data-ready]", state="attached")
    assert page.is_visible("#hint-tutorial")
    page.wait_for_selector("#sp-server:not([hidden])")
    pick(page, "#sp-server", "beta")
    page.wait_for_function("document.querySelector('#hint-tutorial').hidden")
    pick(page, "#sp-server", "alpha")
    page.wait_for_function("!document.querySelector('#hint-tutorial').hidden")
    assert page.errors == []


def test_start_this_tutorial_in_the_help_tab_starts_the_guide(
    server,  # noqa: F811
    page,  # noqa: F811
):
    titled = "t => document.querySelector('.helpv .htitle')?.textContent === t"
    page.goto(server.url)
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="monitors"]')
    page.wait_for_function(titled, arg=TITLE["monitors"])
    assert page.locator("button", has_text="Start this tutorial").is_hidden()
    page.click('.helpv .hrow[data-ref="getting-started"]')
    page.wait_for_function(titled, arg=TITLE["getting-started"])
    page.click("button:has-text('Start this tutorial')")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    assert meta(server, log_id)["agent"] == "opus"  # default_agent
    assert page.errors == []


def test_a_double_click_on_start_this_tutorial_starts_one_guide(
    server,  # noqa: F811
    page,  # noqa: F811
):
    titled = "t => document.querySelector('.helpv .htitle')?.textContent === t"
    page.goto(server.url)
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="getting-started"]')
    page.wait_for_function(titled, arg=TITLE["getting-started"])
    page.dblclick("button:has-text('Start this tutorial')")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    time.sleep(0.5)  # a second guide, had one started, has its tab by now
    assert page.locator("#tablist .tab").count() == 1
    assert (
        len(list((server.root / ".aegis" / "state" / "sessions").glob("*.json"))) == 1
    )
    assert page.errors == []
