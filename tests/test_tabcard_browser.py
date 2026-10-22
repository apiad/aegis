"""The tab card (#586): hovering a session tab opens its Fleet card under it,
with the tab menu's items as icon buttons, and a long press on a phone opens
the same card as a sheet."""

import pytest

from .test_browser import (  # noqa: F401
    browser,
    server,
    spawn,
)
from .test_menu_browser import item_ids

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def tab(key: str) -> str:
    return f"#tablist .tab[data-id='{key}']"


def open_card(pg, key: str) -> None:
    pg.mouse.move(640, 760)  # off the bar, so the hover is a new one
    pg.hover(tab(key))
    pg.wait_for_selector(f".tabcard .card[data-id='{key}']", state="visible")


def test_hover_opens_the_card_and_moving_onto_it_keeps_it(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello")
    page.goto(server.url.split("#")[0] + "#fleet")
    open_card(page, key)
    page.hover(".tabcard .card .ttl")
    page.wait_for_timeout(500)
    assert page.is_visible(".tabcard")
    page.mouse.move(640, 760)
    page.wait_for_selector(".tabcard", state="hidden")
    assert page.errors == []


def test_the_cards_buttons_are_the_tab_menus_items(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello")
    page.click(tab(key), button="right")
    ids = item_ids(page)
    page.keyboard.press("Escape")
    page.mouse.move(640, 760)
    open_card(page, key)
    got = page.eval_on_selector_all(
        ".tabcard .acts button", "bs => bs.map(b => b.dataset.id)"
    )
    assert got == ids
    labels = page.eval_on_selector_all(
        ".tabcard .acts button",
        "bs => bs.map(b => !!b.title && !!b.querySelector('svg'))",
    )
    assert all(labels)
    assert page.errors == []


def test_a_key_closes_the_card_and_still_does_its_job(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello")
    page.goto(server.url.split("#")[0] + "#fleet")
    open_card(page, key)
    page.keyboard.press("Escape")
    page.wait_for_selector(".tabcard", state="hidden")
    open_card(page, key)
    page.keyboard.press("Alt+0")  # the Fleet's key still works with the card open
    page.wait_for_selector(".tabcard", state="hidden")
    assert page.get_attribute("#a2", "data-view") == "fleet"
    assert page.errors == []


def test_archiving_from_the_card_closes_it(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page)
    page.goto(server.url.split("#")[0] + "#fleet")
    open_card(page, key)
    page.click(".tabcard .acts [data-id=archive]")
    page.wait_for_selector(tab(key), state="detached")
    assert page.is_hidden(".tabcard")
    assert page.errors == []


def hold(ctx, pg, sel: str, until: str) -> None:
    """A finger held on `sel` until `until` shows, then lifted."""
    box = pg.locator(sel).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp = ctx.new_cdp_session(pg)
    cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
    )
    pg.wait_for_selector(until, timeout=3000)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    pg.wait_for_timeout(300)


def test_a_long_press_on_a_tab_opens_the_sheet_and_a_card_keeps_its_menu(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(pg)
    pg.goto(server.url.split("#")[0] + "#fleet")
    pg.wait_for_selector(f"#cards .card[data-id='{key}']")
    hold(ctx, pg, f"#cards .card[data-id='{key}']", ".cmenu")
    ids = item_ids(pg)
    pg.keyboard.press("Escape")
    hold(ctx, pg, tab(key), ".tabsheet:not([hidden])")
    assert pg.locator(".cmenu").count() == 0
    assert pg.is_visible(f".tabsheet .card[data-id='{key}']")
    got = pg.eval_on_selector_all(
        ".tabsheet .acts button", "bs => bs.map(b => b.dataset.id)"
    )
    assert got == ids
    heights = pg.eval_on_selector_all(
        ".tabsheet .acts button", "bs => bs.map(b => b.offsetHeight)"
    )
    assert min(heights) >= 44
    assert pg.evaluate("location.hash") == "#fleet"  # the press opened nothing
    pg.tap("#cards")
    pg.wait_for_selector(".tabsheet", state="hidden")
    ctx.close()
    assert errors == []
