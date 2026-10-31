"""The header when the tabs outgrow it: the tab list scrolls, and nothing to its
right gives up any width."""

import pytest

from .test_browser import browser, online, report, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def test_a_full_tab_list_leaves_the_header_buttons_their_width(server, page):  # noqa: F811
    page.goto(server.url)
    online(page)
    buttons = "#schedules-btn,#settings-btn,#files-btn,#journal-btn,#knowledge-btn,#usage-btn,#bell,#help-btn"
    widths = f"[...document.querySelectorAll('{buttons}')].map(b => b.getBoundingClientRect().width)"
    roomy = page.evaluate(widths)
    while page.is_hidden("#tab-more"):
        spawn(page)
    assert page.evaluate(widths) == roomy
    assert min(roomy) >= 30
    version = page.locator("#ver-top")
    page.wait_for_selector("#ver-top:not([hidden])")
    assert version.bounding_box()["height"] < 20, "the version wrapped"
    assert page.errors == []
