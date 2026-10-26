"""The usage page as a person uses it: opened from the palette and the quota
gauge's label, its spend tile following a turn that ends in another tab
within 2 s, and nothing wider than a phone at 390 px (#737)."""

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    WIDER,
    Server,
    browser,
    new_page,
    page,
    phone,
    server,
    spawn,
    turns_done,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

TILE = ".usage .utile[data-key=total] .v"


def spend(pg) -> str:
    return pg.eval_on_selector(TILE, "e => e.firstChild.textContent")


def other_tab(browser, server):  # noqa: F811
    pg = new_page(browser, [])
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    return pg


def a_turn_moves_the_tile(pg, other, cost: str) -> None:
    before = spend(pg)
    spawn(other, f"spend {cost}")
    turns_done(other, 1)
    pg.wait_for_function(
        "([sel, before]) => document.querySelector(sel).firstChild.textContent !== before",
        arg=[TILE, before],
        timeout=2000,
    )


def test_the_page_opens_from_the_palette_and_follows_a_turn(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("Control+k")
    page.fill("#palette-q", "usage")
    page.keyboard.press("Enter")
    page.wait_for_selector("#a2[data-view=usage] " + TILE)
    assert page.evaluate("location.hash") == "#usage"
    assert spend(page) == "$0.00"
    a_turn_moves_the_tile(page, other_tab(browser, server), "0.25")
    assert spend(page) == "$0.25"
    # Every control changes what the page asks for and draws.
    page.click(".usage .useg button[data-key=tokens]")
    page.wait_for_function(
        "document.querySelector('.usage .utile .k').textContent.startsWith('Tokens')"
    )
    page.click(".usage .useg button[data-key=mode]")
    page.wait_for_function(
        "document.querySelector('.usage .pane .h').textContent === 'Stacked by mode'"
    )
    assert page.errors == []


def test_the_gauge_label_opens_it_and_a_phone_fits(server, browser):  # noqa: F811
    errors: list = []
    pg = phone(browser, errors)
    pg.goto(server.url + "#usage")
    pg.wait_for_selector(TILE)
    a_turn_moves_the_tile(pg, other_tab(browser, server), "0.25")
    assert spend(pg) == "$0.25"
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    # The heat grids scroll sideways inside their panel (spec 12).
    assert pg.evaluate(WIDER.replace(".diff')", ".diff, .uscroll')")) == []
    pg.evaluate("location.hash = '#fleet'")
    pg.wait_for_selector("#a2[data-view=fleet]")
    pg.evaluate("document.getElementById('band-quota-link').click()")
    pg.wait_for_selector("#a2[data-view=usage]")
    assert errors == []
