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


def test_guided_tour_in_the_help_tab_starts_a_tutorial_or_a_tour(
    server,  # noqa: F811
    page,  # noqa: F811
):
    titled = "t => document.querySelector('.helpv .htitle')?.textContent === t"
    page.goto(server.url)
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="monitors"]')
    page.wait_for_function(titled, arg=TITLE["monitors"])
    page.click("button:has-text('Guided tour')")
    page.wait_for_selector("#a2[data-view=session]")
    tour = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_NAME, arg="monitors", timeout=8000)
    assert meta(server, tour)["title"] == f"Tour: {TITLE['monitors']}"
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="getting-started"]')
    page.wait_for_function(titled, arg=TITLE["getting-started"])
    page.click("button:has-text('Guided tour')")
    # The help rows set #help=…; wait for the second guide's own #s= hash.
    page.wait_for_function(
        "t => location.hash.startsWith('#s=') && location.hash.slice(3) !== t",
        arg=tour,
    )
    log_id = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    assert meta(server, log_id)["agent"] == "opus"  # default_agent
    assert page.errors == []


def test_a_double_click_on_guided_tour_starts_one_guide(
    server,  # noqa: F811
    page,  # noqa: F811
):
    titled = "t => document.querySelector('.helpv .htitle')?.textContent === t"
    page.goto(server.url)
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="getting-started"]')
    page.wait_for_function(titled, arg=TITLE["getting-started"])
    page.dblclick("button:has-text('Guided tour')")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_function(ENTRIES_NAME, arg="getting-started", timeout=8000)
    time.sleep(0.5)  # a second guide, had one started, has its tab by now
    assert page.locator("#tablist .tab").count() == 1
    assert (
        len(list((server.root / ".aegis" / "state" / "sessions").glob("*.json"))) == 1
    )
    assert page.errors == []


def test_enter_on_a_bare_command_with_an_argument_does_what_it_did(
    server,  # noqa: F811
    page,  # noqa: F811
):
    """The Enter rule now opens choices only for a required argument (`<…>`),
    so /tutorial sends alone. Every other aegis command that takes an argument
    does what it did before that change, checked against its parent."""
    page.goto(server.url)
    spawn(page)
    sent = "document.querySelectorAll('.row.user').length"
    before = page.evaluate(sent)
    expect = {
        "model": ("/model ", ""),  # Enter opens its choices
        "effort": ("/effort ", ""),
        "permission": ("/permission ", ""),
        "rename": ("/rename", "missing_argument: usage: /rename <handle>"),
        "title": ("/title", "missing_argument: usage: /title <text>"),
        "spawn": ("/spawn ", ""),  # its agents since #352
        "help": ("/", ""),  # the menu, with every command
    }
    for name, (line, error) in expect.items():
        page.fill("#input", "")
        page.fill("#input", f"/{name}")
        page.wait_for_selector("#cmd-menu:not([hidden])")
        page.press("#input", "Enter")
        if error:
            # Refusals show as error toasts (#329).
            page.wait_for_function(
                "e => [...document.querySelectorAll('.toast.error .msg')]"
                ".some((m) => m.textContent.startsWith(e))",
                arg=error,
            )
        else:
            page.wait_for_function(
                "l => document.getElementById('input').value === l", arg=line
            )
        assert (page.input_value("#input"), name) == (line, name)
        assert page.is_visible("#cmd-menu"), name
    assert page.evaluate(sent) == before, "none of them reached the agent"
    assert page.errors == []


def test_a_question_typed_in_help_goes_to_an_agent_and_enter_still_searches(
    server,  # noqa: F811
    page,  # noqa: F811
):
    titled = "t => document.querySelector('.helpv .htitle')?.textContent === t"
    q = "can two queues share a worker"
    page.goto(server.url)
    page.click("#help-btn")
    page.click('.helpv .hrow[data-ref="queues"]')
    page.wait_for_function(titled, arg=TITLE["queues"])
    assert page.locator(".helpv .hask").is_hidden()
    page.fill(".helpv .jq", q)
    page.wait_for_selector(".helpv .hask:not([hidden])")
    page.press(".helpv .jq", "Enter")
    page.wait_for_function("() => location.hash.startsWith('#help=')")
    assert page.locator("#tablist .tab").count() == 0
    page.dblclick(".helpv .hask")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_NAME, arg=q, timeout=8000)
    assert meta(server, log_id)["title"] == f"Help: {q}"
    time.sleep(0.5)  # a second session, had the double click started one
    assert page.locator("#tablist .tab").count() == 1
    assert page.errors == []
