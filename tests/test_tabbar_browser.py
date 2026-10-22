"""The tab bar (#586): bare icon buttons, the session panel's toggle in the
transcript's navigation pill, two-line tabs that keep the whole handle, a
strip that scrolls, and the menu at its end."""

import json

import pytest

from .test_browser import (  # noqa: F401
    browser,
    server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

HEADER_ICONS = ["schedules-btn", "settings-btn", "files-btn", "journal-btn"]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def bare(pg, sel: str) -> dict:
    """What a header button draws around its icon."""
    return pg.eval_on_selector(
        sel,
        """b => { const c = getComputedStyle(b);
          const lbl = b.querySelector('.lbl');
          return {bg: c.backgroundColor, border: c.borderTopColor,
                  label: !!lbl && lbl.offsetWidth > 0,
                  icon: !!b.querySelector('svg') && b.querySelector('svg').getBoundingClientRect().width > 0}; }""",
    )


def test_the_header_buttons_are_bare_icons(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    for id in HEADER_ICONS:
        b = bare(page, f"#{id}")
        assert b == {
            "bg": "rgba(0, 0, 0, 0)",
            "border": "rgba(0, 0, 0, 0)",
            "label": False,
            "icon": True,
        }, id
        assert page.get_attribute(f"#{id}", "aria-label")
    assert page.errors == []


def test_files_stays_in_the_header_on_a_phone(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    assert pg.is_visible("#files-btn")
    pg.close()


def test_the_panel_toggle_lives_in_the_nav_pill(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    assert page.is_visible("#nav #nav-side")
    assert not page.is_visible(".tabs #side-btn")
    before = page.get_attribute("#a2", "data-side")
    page.click("#nav-side")
    side = "(document.querySelector('#a2').dataset.side ?? null)"
    page.wait_for_function(f"{side} !== {json.dumps(before)}")
    page.keyboard.press("Alt+b")
    page.wait_for_function(f"{side} === {json.dumps(before)}")
    assert page.errors == []
