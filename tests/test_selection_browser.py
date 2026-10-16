"""Actions on text selected in an assistant reply."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on(
        "console",
        lambda m: (
            m.type == "error"
            and not any(x in m.text for x in EXPECTED_CONSOLE)
            and pg.errors.append(m.text)
        ),
    )
    yield pg
    pg.context.close()


def select_reply(pg) -> str:
    body = pg.locator(".row.prose").last.locator(":scope > .body")
    body.select_text()
    pg.wait_for_selector("#selection-menu:not([hidden])")
    return pg.evaluate("window.getSelection().toString().trim()")


def test_selected_reply_text_offers_contextual_actions_and_fills_composer(  # noqa: F811
    server,  # noqa: F811
    page,
):
    page.goto(server.url)
    spawn(page, "/md A response worth selecting.")

    selected = select_reply(page)
    assert page.locator("#selection-menu button").all_text_contents() == [
        "Add to chat",
        "More details",
        "Ask in side chat",
    ]
    box = page.locator("#selection-menu").bounding_box()
    assert box and 0 <= box["x"] <= 1280 - box["width"]
    assert 0 <= box["y"] <= 800 - box["height"]

    page.fill("#input", "Keep this draft")
    selected = select_reply(page)
    page.locator('[data-selection-act="add"]').click()
    assert page.input_value("#input") == f"Keep this draft\n\n{selected}"
    assert page.evaluate("document.activeElement.id") == "input"
    assert page.is_hidden("#selection-menu")

    page.fill("#input", "")
    select_reply(page)
    page.locator('[data-selection-act="details"]').click()
    assert page.input_value("#input") == f"Tell me more about this:\n\n{selected}"

    page.fill("#input", "")
    select_reply(page)
    page.locator('[data-selection-act="btw"]').click()
    assert page.input_value("#input") == f"/btw Tell me more about this:\n\n{selected}"

    page.locator(".row.user").last.locator(":scope > .body").select_text()
    page.wait_for_function("document.querySelector('#selection-menu').hidden")
    assert page.errors == []
